"""Orchestration Tier 3 — failure isolation.

Provides a circuit-breaker and error-budget layer for agent dispatch.
Each agent gets an independent failure counter; when an agent exceeds
its budget (N failures in M seconds), the circuit opens and rejects
further calls with a clear message instead of letting the agent keep
failing (and potentially cascading).

The circuit auto-resolves after a cooldown period (half-open state),
allowing one probe call through. If the probe succeeds, the circuit
closes; if it fails, the budget resets and the cooldown restarts.

This module is stateless relative to the Pipeline — it stores state
in class-level dicts keyed by agent name, so it survives across
requests but doesn't need persistence.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class CircuitState(Enum):
    CLOSED = "closed"       # Normal operation — calls pass through.
    OPEN = "open"           # Too many failures — calls are rejected.
    HALF_OPEN = "half_open" # Cooldown elapsed — one probe call allowed.


@dataclass
class AgentCircuit:
    """Circuit breaker state for a single agent."""

    state: CircuitState = CircuitState.CLOSED
    failure_count: int = 0
    last_failure_time: float = 0.0
    last_success_time: float = 0.0
    total_failures: int = 0
    total_calls: int = 0
    total_rejected: int = 0


@dataclass
class FailureBudget:
    """Configuration for an agent's failure budget."""

    # Number of failures in the window before the circuit opens.
    max_failures: int = 5
    # Time window in seconds for counting failures.
    window_seconds: float = 300.0  # 5 minutes
    # Cooldown in seconds before trying a probe call.
    cooldown_seconds: float = 60.0


# Default budget for all agents. Override per-agent via AGENT_BUDGETS.
DEFAULT_BUDGET = FailureBudget()

# Per-agent budget overrides. Agents not listed use the default.
AGENT_BUDGETS: dict[str, FailureBudget] = {
    # Reasoning agent gets a tighter budget — it's the most frequently
    # called and a failure there usually means the LLM is down.
    "reasoning": FailureBudget(max_failures=3, window_seconds=120, cooldown_seconds=30),
    # Design agent — slower, more expensive, looser budget.
    "design": FailureBudget(max_failures=3, window_seconds=600, cooldown_seconds=120),
    # Control agent — tool execution failures are expected (permission
    # denied, file not found), so give it a generous budget.
    "control": FailureBudget(max_failures=10, window_seconds=300, cooldown_seconds=30),
    # Ollama Cloud LLM provider: when the cloud quota/subscription fails,
    # every turn otherwise pays the failed attempt before falling back to
    # the local model. After a few consecutive failures the circuit opens
    # and the cloud attempt is skipped entirely for the cooldown period;
    # a probe call then retries and closes the circuit on success.
    "ollama_cloud": FailureBudget(max_failures=3, window_seconds=120, cooldown_seconds=60),
}


class FailureIsolation:
    """Manages circuit breakers for all agents."""

    def __init__(self) -> None:
        self._circuits: dict[str, AgentCircuit] = {}

    def _circuit(self, agent_name: str) -> AgentCircuit:
        if agent_name not in self._circuits:
            self._circuits[agent_name] = AgentCircuit()
        return self._circuits[agent_name]

    def _budget(self, agent_name: str) -> FailureBudget:
        return AGENT_BUDGETS.get(agent_name, DEFAULT_BUDGET)

    def can_call(self, agent_name: str) -> tuple[bool, Optional[str]]:
        """Check whether a call to `agent_name` is allowed.

        Returns (allowed, reason_if_rejected).
        """
        circuit = self._circuit(agent_name)
        budget = self._budget(agent_name)
        now = time.monotonic()

        if circuit.state == CircuitState.CLOSED:
            return True, None

        if circuit.state == CircuitState.OPEN:
            # Check if cooldown has elapsed.
            if now - circuit.last_failure_time >= budget.cooldown_seconds:
                circuit.state = CircuitState.HALF_OPEN
                return True, None
            remaining = budget.cooldown_seconds - (now - circuit.last_failure_time)
            return False, (
                f"Agent '{agent_name}' circuit is OPEN — "
                f"{circuit.failure_count} failures in {budget.window_seconds:.0f}s. "
                f"Retry in {remaining:.0f}s."
            )

        # HALF_OPEN — allow one probe call.
        return True, None

    def record_success(self, agent_name: str) -> None:
        """Record a successful call."""
        circuit = self._circuit(agent_name)
        budget = self._budget(agent_name)
        now = time.monotonic()

        circuit.total_calls += 1
        circuit.last_success_time = now

        if circuit.state == CircuitState.HALF_OPEN:
            # Probe succeeded — close the circuit.
            circuit.state = CircuitState.CLOSED
            circuit.failure_count = 0
        elif circuit.state == CircuitState.CLOSED:
            # Check if failures are outside the window; reset count.
            if now - circuit.last_failure_time > budget.window_seconds:
                circuit.failure_count = 0

    def record_failure(self, agent_name: str) -> None:
        """Record a failed call. May trip the circuit."""
        circuit = self._circuit(agent_name)
        budget = self._budget(agent_name)
        now = time.monotonic()

        circuit.total_calls += 1
        circuit.total_failures += 1
        circuit.last_failure_time = now

        if circuit.state == CircuitState.HALF_OPEN:
            # Probe failed — re-open the circuit.
            circuit.state = CircuitState.OPEN
            circuit.failure_count += 1
            return

        if circuit.state == CircuitState.CLOSED:
            # Check if this failure is within the window.
            if now - circuit.last_failure_time <= budget.window_seconds:
                circuit.failure_count += 1
            else:
                # Outside window — start fresh with this failure.
                circuit.failure_count = 1

            if circuit.failure_count >= budget.max_failures:
                circuit.state = CircuitState.OPEN

    def reject(self, agent_name: str) -> None:
        """Record a rejected call (circuit was open)."""
        circuit = self._circuit(agent_name)
        circuit.total_rejected += 1

    def status(self) -> dict[str, dict]:
        """Return circuit status for all tracked agents."""
        result = {}
        for name, circuit in self._circuits.items():
            budget = self._budget(name)
            result[name] = {
                "state": circuit.state.value,
                "failure_count": circuit.failure_count,
                "total_failures": circuit.total_failures,
                "total_calls": circuit.total_calls,
                "total_rejected": circuit.total_rejected,
                "budget": {
                    "max_failures": budget.max_failures,
                    "window_seconds": budget.window_seconds,
                    "cooldown_seconds": budget.cooldown_seconds,
                },
            }
        return result

    def reset(self, agent_name: Optional[str] = None) -> None:
        """Reset circuit(s). If agent_name is None, reset all."""
        if agent_name:
            self._circuits.pop(agent_name, None)
        else:
            self._circuits.clear()


# Module-level singleton — shared across the Pipeline.
failure_isolation = FailureIsolation()
