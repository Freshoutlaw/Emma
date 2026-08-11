"""Orchestration Tier 5 — handoff recommendation system.

When an agent finishes its work, it can *propose* a next step and a
target agent. The proposal is surfaced to the operator (via the HUD
consent banner) who approves or denies it. No agent silently dispatches
another agent — every handoff requires an explicit human yes.

The handoff model:

1. Agent finishes → returns a HandoffProposal alongside its AgentResult.
2. Router surfaces the proposal as a ``handoff`` SSE event.
3. Operator approves via the consent endpoint.
4. Router dispatches the approved handoff to the target agent.
5. The handoff chain is logged in the audit trail for traceability.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class HandoffProposal:
    """A proposed next step from an agent."""

    # Unique ID for this proposal (used to look it up on approval).
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    # The agent that proposes the handoff.
    source_agent: str = ""
    # The agent that should execute the next step.
    target_agent: str = ""
    # Human-readable description of what the next step would do.
    description: str = ""
    # The message/prompt to send to the target agent.
    message: str = ""
    # When the proposal was created.
    created_at: float = field(default_factory=time.monotonic)
    # Optional context from the source agent's work (e.g. file paths, IDs).
    context: dict = field(default_factory=dict)
    # TTL in seconds — proposals expire after this.
    ttl_seconds: float = 300.0  # 5 minutes

    @property
    def expired(self) -> bool:
        return (time.monotonic() - self.created_at) > self.ttl_seconds

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "source_agent": self.source_agent,
            "target_agent": self.target_agent,
            "description": self.description,
            "message": self.message,
            "context": self.context,
            "created_at": self.created_at,
            "ttl_seconds": self.ttl_seconds,
        }


class HandoffManager:
    """Manages handoff proposals and approvals."""

    def __init__(self) -> None:
        self._pending: dict[str, HandoffProposal] = {}
        self._history: list[dict] = []

    def propose(self, proposal: HandoffProposal) -> HandoffProposal:
        """Register a new handoff proposal. Returns the proposal with its ID."""
        self._pending[proposal.id] = proposal
        return proposal

    def approve(self, proposal_id: str) -> Optional[HandoffProposal]:
        """Approve and consume a pending proposal. Returns None if expired/missing."""
        proposal = self._pending.pop(proposal_id, None)
        if proposal is None or proposal.expired:
            if proposal:
                self._record(proposal, "expired")
            return None
        self._record(proposal, "approved")
        return proposal

    def deny(self, proposal_id: str) -> bool:
        """Deny a pending proposal. Returns True if it existed."""
        proposal = self._pending.pop(proposal_id, None)
        if proposal is None:
            return False
        self._record(proposal, "denied")
        return True

    def pending(self) -> list[dict]:
        """Return all non-expired pending proposals."""
        # Clean up expired ones.
        expired_ids = [pid for pid, p in self._pending.items() if p.expired]
        for pid in expired_ids:
            self._record(self._pending.pop(pid), "expired")
        return [p.to_dict() for p in self._pending.values()]

    def _record(self, proposal: HandoffProposal, outcome: str) -> None:
        self._history.append({
            "id": proposal.id,
            "source": proposal.source_agent,
            "target": proposal.target_agent,
            "outcome": outcome,
            "description": proposal.description,
        })
        # Keep history bounded.
        if len(self._history) > 100:
            self._history = self._history[-50:]

    def history(self) -> list[dict]:
        return list(self._history)


def create_handoff(
    source: str,
    target: str,
    description: str,
    message: str,
    context: Optional[dict] = None,
) -> HandoffProposal:
    """Convenience factory for creating a handoff proposal."""
    return HandoffProposal(
        source_agent=source,
        target_agent=target,
        description=description,
        message=message,
        context=context or {},
    )
