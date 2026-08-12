"""Concurrent-turn load benchmark for the async LLM path.

Drives real turns through the real Pipeline (AgentRouter.run) with a
controllable fake LLM, and reports:

  - turns/sec under concurrency
  - per-turn latency (mean / p50 / p95)
  - event-loop responsiveness: a heartbeat task that ticks every 10ms,
    reporting the max observed gap between ticks.  This is the metric that
    catches a frozen event loop — the pre-fix synchronous complete() blocked
    the loop for the entire LLM call, so concurrent turns serialized and the
    heartbeat showed multi-hundred-ms gaps.  With the async fix, the loop
    stays responsive and turns overlap.

Usage:
  python benchmarks/turn_load.py                       # async (fixed) path
  python benchmarks/turn_load.py --blocking            # simulate pre-fix sync complete
  python benchmarks/turn_load.py --both                # run both, print comparison

Options:
  --workers N      concurrent turn workers      (default 6)
  --turns N        turns per worker             (default 6)
  --latency S      fake LLM per-call latency s  (default 0.2)
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import time
from typing import AsyncIterator, Optional

from backend.config import Settings
from agents.router import Pipeline

# Each reasoning turn makes 2 LLM calls (plan + synthesize), so a turn's
# serial LLM time is roughly 2 * latency.
LLM_CALLS_PER_TURN = 2

MESSAGES = [
    "hello",
    "hi there",
    "what's up",
    "good morning",
    "hey emma",
]


class FakeLLM:
    """Async LLM: awaits the simulated latency, keeping the loop responsive."""

    def __init__(self, latency: float) -> None:
        self.latency = latency

    def route(self) -> str:
        return "local"

    async def complete(self, messages: list, temperature: float = 0.7, max_tokens: int = 4096) -> str:
        await asyncio.sleep(self.latency)
        return "Hello! How can I help you today?"

    async def stream(self, messages: list, temperature: float = 0.7, max_tokens: int = 4096) -> AsyncIterator[str]:
        await asyncio.sleep(self.latency / 2)
        yield "Hello! "
        await asyncio.sleep(self.latency / 2)
        yield "How can I help you today?"


class BlockingLLM:
    """Pre-fix simulation: sync time.sleep inside an async function.

    time.sleep blocks the event loop thread for the whole call, so concurrent
    turns serialize and the heartbeat stalls — exactly what the async
    complete() fix eliminated.
    """

    def __init__(self, latency: float) -> None:
        self.latency = latency

    def route(self) -> str:
        return "local"

    async def complete(self, messages: list, temperature: float = 0.7, max_tokens: int = 4096) -> str:
        time.sleep(self.latency)
        return "Hello! How can I help you today?"

    async def stream(self, messages: list, temperature: float = 0.7, max_tokens: int = 4096) -> AsyncIterator[str]:
        time.sleep(self.latency / 2)
        yield "Hello! "
        time.sleep(self.latency / 2)
        yield "How can I help you today?"


class FakeEpisodic:
    """Keeps the benchmark on the LLM path: no real embeddings or storage."""

    async def recall(self, query: str, k: Optional[int] = None) -> list[dict]:
        return []

    async def remember(self, content: str, kind: str = "episode", payload: dict | None = None) -> str:
        return "bench-episode"


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * p / 100
    f = int(k)
    c = min(f + 1, len(s) - 1)
    d = k - f
    return s[f] * (1 - d) + s[c] * d


async def _heartbeat(gaps: list[float], stop: asyncio.Event, interval: float = 0.01) -> None:
    """Tick every `interval` s and record the gap since the last tick."""
    last = time.perf_counter()
    while not stop.is_set():
        await asyncio.sleep(interval)
        now = time.perf_counter()
        gaps.append(now - last)
        last = now


async def run_mode(label: str, workers: int, turns: int, latency: float, blocking: bool) -> dict:
    llm_cls = BlockingLLM if blocking else FakeLLM
    pipeline = Pipeline(Settings())
    pipeline.llm = llm_cls(latency)
    pipeline.episodic = FakeEpisodic()
    router = pipeline.router

    gaps: list[float] = []
    stop = asyncio.Event()
    hb = asyncio.create_task(_heartbeat(gaps, stop))

    latencies: list[float] = []
    turn_count = 0

    async def worker(wid: int) -> None:
        nonlocal turn_count
        for i in range(turns):
            message = MESSAGES[(wid * turns + i) % len(MESSAGES)]
            t0 = time.perf_counter()
            result = await router.run(message)
            latencies.append(time.perf_counter() - t0)
            turn_count += 1
            if not result.ok:
                print(f"  [{label}] WARNING: turn failed: {result.error}")

    start = time.perf_counter()
    await asyncio.gather(*(worker(w) for w in range(workers)))
    wall = time.perf_counter() - start

    stop.set()
    await hb

    total = workers * turns
    report = {
        "label": label,
        "turns": total,
        "wall": wall,
        "turns_per_sec": total / wall,
        "lat_mean": statistics.mean(latencies) if latencies else 0.0,
        "lat_p50": _percentile(latencies, 50),
        "lat_p95": _percentile(latencies, 95),
        "hb_max_gap": max(gaps) if gaps else 0.0,
        "hb_mean_gap": statistics.mean(gaps) if gaps else 0.0,
        "llm_latency": latency,
        "llm_calls_per_turn": LLM_CALLS_PER_TURN,
    }

    await pipeline.close()
    return report


def _print(report: dict) -> None:
    print(f"\n=== {report['label']} ===")
    print(f"  turns:            {report['turns']}")
    print(f"  wall time:        {report['wall']:.2f}s")
    print(f"  turns/sec:        {report['turns_per_sec']:.2f}")
    print(f"  per-turn latency: mean {report['lat_mean']*1000:.0f}ms | "
          f"p50 {report['lat_p50']*1000:.0f}ms | p95 {report['lat_p95']*1000:.0f}ms")
    print(f"  heartbeat gap:    max {report['hb_max_gap']*1000:.0f}ms | "
          f"mean {report['hb_mean_gap']*1000:.1f}ms  (10ms tick)")


def _compare(async_r: dict, blocking_r: dict) -> None:
    print("\n=== async fix vs. pre-fix (blocking) simulation ===")
    print(f"  throughput: {async_r['turns_per_sec']:.2f} vs {blocking_r['turns_per_sec']:.2f} turns/sec "
          f"({async_r['turns_per_sec'] / blocking_r['turns_per_sec']:.1f}x)")
    print(f"  heartbeat max gap: {async_r['hb_max_gap']*1000:.0f}ms vs {blocking_r['hb_max_gap']*1000:.0f}ms "
          f"({blocking_r['hb_max_gap'] / async_r['hb_max_gap']:.0f}x stall — the loop freezes that long)")
    speedup = blocking_r['wall'] / async_r['wall']
    print(f"  wall time for {async_r['turns']} turns: {async_r['wall']:.1f}s vs {blocking_r['wall']:.1f}s "
          f"({speedup:.1f}x faster)")


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--turns", type=int, default=6)
    parser.add_argument("--latency", type=float, default=0.2)
    parser.add_argument("--blocking", action="store_true", help="simulate the pre-fix sync complete()")
    parser.add_argument("--both", action="store_true", help="run async then blocking, print comparison")
    args = parser.parse_args()

    if args.both:
        async_r = await run_mode("async (fixed)", args.workers, args.turns, args.latency, blocking=False)
        _print(async_r)
        blocking_r = await run_mode("pre-fix (blocking sim)", args.workers, args.turns, args.latency, blocking=True)
        _print(blocking_r)
        _compare(async_r, blocking_r)
    else:
        report = await run_mode(
            "pre-fix (blocking sim)" if args.blocking else "async (fixed)",
            args.workers, args.turns, args.latency, blocking=args.blocking,
        )
        _print(report)


if __name__ == "__main__":
    asyncio.run(main())
