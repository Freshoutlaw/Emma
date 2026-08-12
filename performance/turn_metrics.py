"""Per-turn latency and LLM routing counters, exposed via /api/performance.

Lets the running app answer "how fast are turns, and who answered them?":

- turns            — count + latency (avg / p50 / p95) broken down by intent
- classify         — LLM intent-classification calls vs. the short-message
                     fast path, so the classify optimization is observable
- llm_routing      — local vs. cloud completes/streams, plus local failures
                     (the async complete fix and local-first routing make the
                     local:cloud ratio the headline health signal)

Everything is thread-safe (used from async turns, the uvicorn workers, and
the sync performance-monitor thread) and bounded: only the last `max_samples`
latencies are kept, so it cannot grow without limit.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Optional


class TurnMetrics:
    """In-process, thread-safe collector for turn and routing statistics."""

    def __init__(self, max_samples: int = 200) -> None:
        self._lock = threading.Lock()
        self._latencies: deque[float] = deque(maxlen=max_samples)
        self._turns_by_intent: dict[str, int] = {}
        self._turn_count = 0
        self._classify_llm = 0
        self._classify_fast = 0
        self._local_calls = 0
        self._local_failures = 0
        self._cloud_calls = 0

    # ---------------------------------------------------------------- record
    def record_turn(self, intent: str, latency: float) -> None:
        """Record one completed turn (intent + wall-clock latency in seconds)."""
        with self._lock:
            self._turn_count += 1
            self._turns_by_intent[intent] = self._turns_by_intent.get(intent, 0) + 1
            self._latencies.append(latency)

    def record_classify(self, *, fast_path: bool) -> None:
        """Record whether intent classification consulted the LLM.

        fast_path=True means the classification returned without an LLM call
        (the short-message fast path); fast_path=False means the LLM was
        actually consulted for the intent decision.
        """
        with self._lock:
            if fast_path:
                self._classify_fast += 1
            else:
                self._classify_llm += 1

    def record_llm_call(self, provider: str, ok: bool) -> None:
        """Record which LLM provider actually served a complete/stream call.

        provider is "local" or "cloud".  ok=False on "local" means the local
        attempt failed and (usually) fell through to cloud — the fallback
        rate is observable as local_failures vs. cloud_calls.
        """
        with self._lock:
            if provider == "local":
                if ok:
                    self._local_calls += 1
                else:
                    self._local_failures += 1
            elif provider == "cloud":
                self._cloud_calls += 1

    # ---------------------------------------------------------------- report
    @staticmethod
    def _percentile(values: list[float], p: float) -> Optional[float]:
        """Linear-interpolated percentile (matches performance.monitor)."""
        if not values:
            return None
        sorted_values = sorted(values)
        k = (len(sorted_values) - 1) * p / 100
        f = int(k)
        c = min(f + 1, len(sorted_values) - 1)
        if f == c:
            return sorted_values[f]
        d = k - f
        return sorted_values[f] * (1 - d) + sorted_values[c] * d

    def snapshot(self) -> dict:
        """Point-in-time summary suitable for /api/performance/stats."""
        with self._lock:
            latencies = list(self._latencies)
            count = self._turn_count
            by_intent = dict(self._turns_by_intent)
            classify_llm = self._classify_llm
            classify_fast = self._classify_fast
            local_calls = self._local_calls
            local_failures = self._local_failures
            cloud_calls = self._cloud_calls

        avg = sum(latencies) / len(latencies) if latencies else None
        return {
            "count": count,
            "latency_seconds": {
                "avg": avg,
                "p50": self._percentile(latencies, 50),
                "p95": self._percentile(latencies, 95),
                "samples": len(latencies),
            },
            "turns_by_intent": by_intent,
            "classify": {
                "llm_calls": classify_llm,
                "fast_path": classify_fast,
            },
            "llm_routing": {
                "local_calls": local_calls,
                "local_failures": local_failures,
                "cloud_calls": cloud_calls,
            },
        }

    def reset(self) -> None:
        """Zero all counters (wired to POST /api/performance/clear)."""
        with self._lock:
            self._latencies.clear()
            self._turns_by_intent.clear()
            self._turn_count = 0
            self._classify_llm = 0
            self._classify_fast = 0
            self._local_calls = 0
            self._local_failures = 0
            self._cloud_calls = 0


# Module singleton — one per process, imported by the router, LLM router, and
# the performance endpoint.
turn_metrics = TurnMetrics()
