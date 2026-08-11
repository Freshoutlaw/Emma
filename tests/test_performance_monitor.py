"""Tests for the performance monitor — summary liveness, percentiles, decorator."""

import asyncio
import threading

from performance.monitor import PerformanceMonitor, perf_monitor, track_latency


def test_get_metrics_summary_does_not_deadlock():
    """Regression: the summary used to re-acquire the non-reentrant lock via
    get_latency_stats() while holding it, deadlocking the endpoint."""
    pm = PerformanceMonitor()
    pm.record_latency("op", 5.0)
    result = {}

    def call():
        try:
            result["summary"] = pm.get_metrics_summary()
        except Exception as exc:
            result["error"] = exc

    thread = threading.Thread(target=call, daemon=True)
    thread.start()
    thread.join(timeout=3)
    assert not thread.is_alive(), "get_metrics_summary deadlocked (reentrant lock regression)"
    assert "summary" in result
    summary = result["summary"]
    assert summary["latencies_count"] == 1
    assert summary["latency_stats"]["count"] == 1


def test_latency_stats_percentiles():
    pm = PerformanceMonitor()
    for value in [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]:
        pm.record_latency("op", value)

    stats = pm.get_latency_stats("op")
    assert stats["count"] == 10
    assert stats["min"] == 1
    assert stats["max"] == 10
    assert stats["mean"] == 5.5
    assert abs(stats["p50"] - 5.5) < 1e-9
    assert abs(stats["p95"] - 9.55) < 1e-9
    assert abs(stats["p99"] - 9.91) < 1e-9


def test_latency_stats_empty_without_operations():
    pm = PerformanceMonitor()
    assert pm.get_latency_stats("nothing") == {}
    assert pm.get_latency_stats() == {}


def test_latency_stats_filtered_by_operation():
    pm = PerformanceMonitor()
    pm.record_latency("a", 1.0)
    pm.record_latency("b", 9.0)
    assert pm.get_latency_stats("a") == {
        "count": 1, "min": 1.0, "max": 1.0, "mean": 1.0,
        "p50": 1.0, "p95": 1.0, "p99": 1.0,
    }
    assert pm.get_latency_stats()["count"] == 2


def test_counters_and_gauges_and_clear():
    pm = PerformanceMonitor()
    pm.increment_counter("tokens", 3)
    pm.increment_counter("tokens")
    pm.set_gauge("cache_size", 12.5)
    assert pm.get_counter("tokens") == 4
    assert pm.get_gauge("cache_size") == 12.5
    assert pm.get_counter("absent") == 0
    assert pm.get_gauge("absent") is None

    pm.clear()
    assert pm.get_counter("tokens") == 0
    assert pm.get_metrics_summary()["latencies_count"] == 0


def test_track_latency_records_async_sync_and_errors():
    perf_monitor.clear()

    @track_latency("async_op")
    async def aop():
        return 42

    @track_latency("sync_op")
    def sop():
        return "ok"

    @track_latency("error_op")
    async def eop():
        raise ValueError("boom")

    async def run():
        assert await aop() == 42
        assert sop() == "ok"
        try:
            await eop()
        except ValueError:
            pass

    asyncio.run(run())
    assert perf_monitor.get_latency_stats("async_op")["count"] == 1
    assert perf_monitor.get_latency_stats("sync_op")["count"] == 1
    assert perf_monitor.get_latency_stats("error_op_error")["count"] == 1
    perf_monitor.clear()
