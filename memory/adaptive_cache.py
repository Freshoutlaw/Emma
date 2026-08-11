"""Adaptive Caching - Smart cache management with adaptive TTL.

OPTIMIZATIONS:
- Adaptive TTL based on access patterns
- Cache size management with eviction policies
- Hit rate tracking for optimization
"""

from __future__ import annotations

import threading
import time
from typing import Any, Optional, Dict
from collections import OrderedDict
from dataclasses import dataclass
from enum import Enum


class EvictionPolicy(Enum):
    LRU = "least_recently_used"
    LFU = "least_frequently_used"
    FIFO = "first_in_first_out"


@dataclass
class CacheEntry:
    value: Any
    timestamp: float
    access_count: int
    ttl: float
    initial_ttl: float


class AdaptiveCache:
    """Adaptive cache with smart TTL and eviction policies."""
    
    def __init__(
        self,
        max_size: int = 1000,
        default_ttl: float = 300.0,
        eviction_policy: EvictionPolicy = EvictionPolicy.LRU,
        adaptive_ttl: bool = True
    ) -> None:
        self.max_size = max_size
        self.default_ttl = default_ttl
        self.eviction_policy = eviction_policy
        self.adaptive_ttl = adaptive_ttl
        self._cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self._hits = 0
        self._misses = 0
        self._lock = threading.Lock()
    
    def get(self, key: str) -> Optional[Any]:
        """Get value from cache with hit tracking."""
        entry = self._cache.get(key)
        if entry is None:
            self._misses += 1
            return None
        
        # Check if expired
        if time.monotonic() - entry.timestamp > entry.ttl:
            del self._cache[key]
            self._misses += 1
            return None
        
        # Update access pattern
        entry.access_count += 1
        if self.eviction_policy == EvictionPolicy.LRU:
            self._cache.move_to_end(key)
        
        # Adaptive TTL: increase TTL for frequently accessed items
        if self.adaptive_ttl and entry.access_count > 5:
            entry.ttl = min(entry.ttl * 1.5, entry.initial_ttl * 10)
        
        self._hits += 1
        return entry.value
    
    def set(self, key: str, value: Any, ttl: Optional[float] = None) -> None:
        """Set value in cache with adaptive sizing."""
        if ttl is None:
            ttl = self.default_ttl
        
        # Evict if at capacity
        if len(self._cache) >= self.max_size and key not in self._cache:
            self._evict()
        
        entry = CacheEntry(
            value=value,
            timestamp=time.monotonic(),
            access_count=0,
            ttl=ttl,
            initial_ttl=ttl
        )
        self._cache[key] = entry
        if self.eviction_policy == EvictionPolicy.LRU:
            self._cache.move_to_end(key)
    
    def _evict(self) -> None:
        """Evict entries based on policy."""
        if not self._cache:
            return
        
        if self.eviction_policy == EvictionPolicy.LRU:
            # Remove oldest (first in OrderedDict)
            self._cache.popitem(last=False)
        elif self.eviction_policy == EvictionPolicy.LFU:
            # Remove least frequently accessed
            min_access = min(e.access_count for e in self._cache.values())
            for key in list(self._cache.keys()):
                if self._cache[key].access_count == min_access:
                    del self._cache[key]
                    break
        elif self.eviction_policy == EvictionPolicy.FIFO:
            self._cache.popitem(last=False)
    
    def clear(self) -> None:
        """Clear all cache entries."""
        self._cache.clear()
        self._hits = 0
        self._misses = 0
    
    def stats(self) -> Dict[str, Any]:
        """Get cache statistics."""
        total = self._hits + self._misses
        hit_rate = self._hits / total if total > 0 else 0
        return {
            "size": len(self._cache),
            "max_size": self.max_size,
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": hit_rate,
            "eviction_policy": self.eviction_policy.value
        }
    
    def cleanup_expired(self) -> int:
        """Remove expired entries, return count removed."""
        now = time.monotonic()
        expired_keys = [
            key for key, entry in self._cache.items()
            if now - entry.timestamp > entry.ttl
        ]
        for key in expired_keys:
            del self._cache[key]
        return len(expired_keys)
