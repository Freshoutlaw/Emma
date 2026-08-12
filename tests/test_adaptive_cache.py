"""Unit tests for AdaptiveCache — eviction policies, TTL, adaptive TTL, stats,
and the real-lock regression (the instance lock must actually serialize access)."""

import threading
import time

from memory.adaptive_cache import AdaptiveCache, EvictionPolicy


class _RecordingLock:
    """Wraps a threading.Lock and counts acquire/release calls, so a test can
    prove the cache methods actually use the instance lock."""

    def __init__(self):
        self._inner = threading.Lock()
        self.acquires = 0
        self.releases = 0

    def acquire(self, *args, **kwargs):
        self.acquires += 1
        return self._inner.acquire(*args, **kwargs)

    def release(self):
        self.releases += 1
        self._inner.release()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *exc):
        self.release()
        return False


# ------------------------------------------------------------------ lock
def test_lock_is_actually_acquired():
    """Regression: AdaptiveCache created a threading.Lock but no method ever
    acquired it, so concurrent get/set could corrupt the OrderedDict.  Every
    mutating/reading method must serialize on the instance lock."""
    cache = AdaptiveCache(max_size=2)
    recorder = _RecordingLock()
    cache._lock = recorder

    cache.set("a", 1)
    cache.get("a")
    cache.get("missing")
    cache.set("b", 2)
    cache.set("c", 3)  # at capacity -> triggers eviction
    cache.cleanup_expired()
    cache.stats()

    assert recorder.acquires > 0, "cache methods never acquire the lock"
    assert recorder.releases == recorder.acquires, "every acquire must release"


def test_concurrent_threads_do_not_corrupt_cache():
    """Smoke test: mixed get/set/cleanup from several threads on a tiny LFU
    cache (eviction scans and deletes) must not raise or break invariants."""
    cache = AdaptiveCache(max_size=5, eviction_policy=EvictionPolicy.LFU)
    errors = []

    def worker(seed):
        try:
            for i in range(300):
                key = f"k{(seed + i) % 7}"
                if i % 3 == 0:
                    cache.set(key, i)
                elif i % 3 == 1:
                    cache.get(key)
                else:
                    cache.cleanup_expired()
        except Exception as exc:  # pragma: no cover - failure path
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(s,)) for s in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"concurrent access raised: {errors}"
    assert cache.stats()["size"] <= 5


# ------------------------------------------------------------------ eviction
def test_lru_eviction_evicts_least_recently_used():
    cache = AdaptiveCache(max_size=2, eviction_policy=EvictionPolicy.LRU)
    cache.set("a", 1)
    cache.set("b", 2)
    cache.get("a")  # 'a' becomes the most recently used
    cache.set("c", 3)  # evicts 'b'
    assert cache.get("b") is None
    assert cache.get("a") == 1
    assert cache.get("c") == 3


def test_lfu_eviction_evicts_least_frequently_used():
    cache = AdaptiveCache(max_size=2, eviction_policy=EvictionPolicy.LFU)
    cache.set("a", 1)
    cache.set("b", 2)
    cache.get("a")
    cache.get("a")  # 'a' used twice
    cache.get("b")  # 'b' used once
    cache.set("c", 3)  # evicts 'b' (least frequently used)
    assert cache.get("b") is None
    assert cache.get("a") == 1
    assert cache.get("c") == 3


def test_fifo_eviction_evicts_oldest_inserted():
    cache = AdaptiveCache(max_size=2, eviction_policy=EvictionPolicy.FIFO)
    cache.set("a", 1)
    cache.set("b", 2)
    cache.get("a")  # FIFO ignores recency
    cache.set("c", 3)  # evicts 'a' (oldest inserted)
    assert cache.get("a") is None
    assert cache.get("b") == 2
    assert cache.get("c") == 3


def test_overwrite_at_capacity_does_not_evict():
    cache = AdaptiveCache(max_size=2, eviction_policy=EvictionPolicy.LRU)
    cache.set("a", 1)
    cache.set("b", 2)
    cache.set("a", 10)  # overwrite, not an insert -> no eviction
    assert cache.stats()["size"] == 2
    assert cache.get("a") == 10
    assert cache.get("b") == 2


# ------------------------------------------------------------------ TTL
def test_ttl_expired_entry_is_a_miss_and_removed():
    cache = AdaptiveCache(default_ttl=300.0)
    cache.set("k", "v")
    cache._cache["k"].timestamp -= 301  # age past the TTL
    assert cache.get("k") is None
    assert cache.stats()["misses"] == 1
    assert "k" not in cache._cache


def test_cleanup_expired_removes_and_counts():
    cache = AdaptiveCache(default_ttl=300.0)
    cache.set("fresh", 1)
    cache.set("stale1", 2)
    cache.set("stale2", 3)
    cache._cache["stale1"].timestamp -= 301
    cache._cache["stale2"].timestamp -= 301
    removed = cache.cleanup_expired()
    assert removed == 2
    assert "stale1" not in cache._cache
    assert "stale2" not in cache._cache
    assert "fresh" in cache._cache


def test_ttl_expiry_with_real_time():
    cache = AdaptiveCache()
    cache.set("k", "v", ttl=0.02)
    assert cache.get("k") == "v"
    time.sleep(0.05)
    assert cache.get("k") is None


# ------------------------------------------------------------------ adaptive TTL
def test_adaptive_ttl_grows_after_frequent_access():
    cache = AdaptiveCache(default_ttl=100.0, adaptive_ttl=True)
    cache.set("k", "v")
    for _ in range(6):
        cache.get("k")
    # 6th access pushes access_count past 5 -> ttl = 100 * 1.5
    assert cache._cache["k"].ttl == 150.0


def test_adaptive_ttl_capped_at_ten_times_initial():
    cache = AdaptiveCache(default_ttl=1000.0, adaptive_ttl=True)
    cache.set("k", "v")
    for _ in range(12):
        cache.get("k")
    assert cache._cache["k"].ttl == 10000.0  # initial_ttl * 10 cap


def test_adaptive_ttl_disabled_keeps_ttl_constant():
    cache = AdaptiveCache(default_ttl=100.0, adaptive_ttl=False)
    cache.set("k", "v")
    for _ in range(10):
        cache.get("k")
    assert cache._cache["k"].ttl == 100.0


# ------------------------------------------------------------------ stats
def test_hit_rate_stats_counts_hits_and_misses():
    cache = AdaptiveCache()
    cache.set("a", 1)
    cache.get("a")  # hit
    cache.get("a")  # hit
    cache.get("nope")  # miss
    stats = cache.stats()
    assert stats["hits"] == 2
    assert stats["misses"] == 1
    assert abs(stats["hit_rate"] - 2 / 3) < 1e-9


def test_clear_resets_entries_and_stats():
    cache = AdaptiveCache()
    cache.set("a", 1)
    cache.get("a")
    cache.get("b")  # miss
    cache.clear()
    stats = cache.stats()
    assert stats["size"] == 0
    assert stats["hits"] == 0
    assert stats["misses"] == 0
    assert stats["hit_rate"] == 0.0


def test_empty_cache_stats():
    stats = AdaptiveCache().stats()
    assert stats["size"] == 0
    assert stats["hit_rate"] == 0.0
