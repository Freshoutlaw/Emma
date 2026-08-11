"""Performance Monitor - Track and analyze system performance.

OPTIMIZATIONS:
- Performance metrics collection
- Latency tracking
- Cache hit rate monitoring
- Resource usage tracking
"""

from __future__ import annotations

import asyncio
import time
import threading
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field
from collections import deque
from functools import wraps


@dataclass
class Metric:
    name: str
    value: float
    timestamp: float
    tags: Dict[str, str] = field(default_factory=dict)


@dataclass
class LatencyMetric:
    operation: str
    latency_ms: float
    timestamp: float


class PerformanceMonitor:
    """Monitor system performance metrics."""
    
    def __init__(self, max_history: int = 1000) -> None:
        self.max_history = max_history
        self._metrics: deque[Metric] = deque(maxlen=max_history)
        self._latencies: deque[LatencyMetric] = deque(maxlen=max_history)
        self._counters: Dict[str, int] = {}
        self._gauges: Dict[str, float] = {}
        self._lock = threading.Lock()
    
    def record_metric(self, name: str, value: float, tags: Optional[Dict[str, str]] = None) -> None:
        """Record a metric value."""
        with self._lock:
            self._metrics.append(Metric(
                name=name,
                value=value,
                timestamp=time.monotonic(),
                tags=tags or {}
            ))
    
    def record_latency(self, operation: str, latency_ms: float) -> None:
        """Record operation latency."""
        with self._lock:
            self._latencies.append(LatencyMetric(
                operation=operation,
                latency_ms=latency_ms,
                timestamp=time.monotonic()
            ))
    
    def increment_counter(self, name: str, value: int = 1) -> None:
        """Increment a counter."""
        with self._lock:
            self._counters[name] = self._counters.get(name, 0) + value
    
    def set_gauge(self, name: str, value: float) -> None:
        """Set a gauge value."""
        with self._lock:
            self._gauges[name] = value
    
    def get_counter(self, name: str) -> int:
        """Get counter value."""
        with self._lock:
            return self._counters.get(name, 0)
    
    def get_gauge(self, name: str) -> Optional[float]:
        """Get gauge value."""
        with self._lock:
            return self._gauges.get(name)
    
    def get_latency_stats(self, operation: Optional[str] = None) -> Dict[str, float]:
        """Get latency statistics for an operation or all operations."""
        with self._lock:
            latencies = [
                l for l in self._latencies
                if operation is None or l.operation == operation
            ]
        
        if not latencies:
            return {}
        
        values = [l.latency_ms for l in latencies]
        return {
            "count": len(values),
            "min": min(values),
            "max": max(values),
            "mean": sum(values) / len(values),
            "p50": self._percentile(values, 50),
            "p95": self._percentile(values, 95),
            "p99": self._percentile(values, 99),
        }
    
    @staticmethod
    def _percentile(values: List[float], p: int) -> float:
        """Calculate percentile."""
        if not values:
            return 0.0
        sorted_values = sorted(values)
        k = (len(sorted_values) - 1) * p / 100
        f = int(k)
        c = f + 1 if f + 1 < len(sorted_values) else f
        if f == c:
            return sorted_values[f]
        d = k - f
        return sorted_values[f] * (1 - d) + sorted_values[c] * d
    
    def get_metrics_summary(self) -> Dict[str, Any]:
        """Get summary of all metrics."""
        with self._lock:
            metrics_count = len(self._metrics)
            latencies_count = len(self._latencies)
            counters = dict(self._counters)
            gauges = dict(self._gauges)
        # NOTE: computed outside the lock — get_latency_stats() acquires the
        # same (non-reentrant) lock and would deadlock if called inside it.
        return {
            "metrics_count": metrics_count,
            "latencies_count": latencies_count,
            "counters": counters,
            "gauges": gauges,
            "latency_stats": self.get_latency_stats()
        }
    
    def clear(self) -> None:
        """Clear all metrics."""
        with self._lock:
            self._metrics.clear()
            self._latencies.clear()
            self._counters.clear()
            self._gauges.clear()


# Global performance monitor instance
perf_monitor = PerformanceMonitor()


def track_latency(operation: str):
    """Decorator to track operation latency."""
    def decorator(func):
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            start = time.perf_counter()
            try:
                result = await func(*args, **kwargs)
                latency_ms = (time.perf_counter() - start) * 1000
                perf_monitor.record_latency(operation, latency_ms)
                return result
            except Exception as e:
                latency_ms = (time.perf_counter() - start) * 1000
                perf_monitor.record_latency(f"{operation}_error", latency_ms)
                raise
        
        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            start = time.perf_counter()
            try:
                result = func(*args, **kwargs)
                latency_ms = (time.perf_counter() - start) * 1000
                perf_monitor.record_latency(operation, latency_ms)
                return result
            except Exception as e:
                latency_ms = (time.perf_counter() - start) * 1000
                perf_monitor.record_latency(f"{operation}_error", latency_ms)
                raise
        
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper
    
    return decorator


# No-op decorator for when performance monitoring is not available
def no_op_latency(operation: str):
    """No-op decorator when performance monitoring is disabled."""
    def decorator(func):
        return func
    return decorator
