"""Speculative Executor - Predict and execute likely operations ahead of time.

OPTIMIZATIONS:
- Predict likely next operations
- Pre-execute for faster response
- Cache speculative results
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Callable, Dict, List, Optional
from collections import defaultdict
from dataclasses import dataclass


@dataclass
class SpeculativeTask:
    pattern: str
    probability: float
    executor: Callable
    cooldown: float = 5.0


class SpeculativeExecutor:
    """Execute likely operations speculatively based on patterns."""
    
    def __init__(self, max_cache_size: int = 100) -> None:
        self.max_cache_size = max_cache_size
        self._patterns: Dict[str, SpeculativeTask] = {}
        self._cache: Dict[str, Any] = {}
        self._cache_timestamps: Dict[str, float] = {}
        self._pattern_counts: Dict[str, int] = defaultdict(int)
        self._last_execution: Dict[str, float] = {}
    
    def register_pattern(
        self,
        pattern: str,
        executor: Callable,
        initial_probability: float = 0.1,
        cooldown: float = 5.0
    ) -> None:
        """Register a speculative execution pattern."""
        self._patterns[pattern] = SpeculativeTask(
            pattern=pattern,
            probability=initial_probability,
            executor=executor,
            cooldown=cooldown
        )
    
    def record_pattern(self, pattern: str) -> None:
        """Record when a pattern occurs to update probabilities."""
        self._pattern_counts[pattern] += 1
        total = sum(self._pattern_counts.values())
        
        # Update probability based on frequency
        if pattern in self._patterns:
            self._patterns[pattern].probability = self._pattern_counts[pattern] / total
    
    async def speculate(self, context: Dict[str, Any]) -> List[str]:
        """Execute speculative tasks based on context."""
        executed = []
        now = time.monotonic()
        
        for pattern, task in self._patterns.items():
            # Check cooldown
            if pattern in self._last_execution:
                if now - self._last_execution[pattern] < task.cooldown:
                    continue
            
            # Check probability threshold
            if task.probability < 0.3:  # Only speculate if reasonably likely
                continue
            
            # Check cache
            cache_key = f"{pattern}_{hash(str(context))}"
            if cache_key in self._cache:
                age = now - self._cache_timestamps[cache_key]
                if age < task.cooldown:
                    continue  # Still fresh
            
            # Execute speculatively
            try:
                result = await task.executor(context)
                self._cache[cache_key] = result
                self._cache_timestamps[cache_key] = now
                self._last_execution[pattern] = now
                executed.append(pattern)
                
                # Clean old cache entries
                if len(self._cache) > self.max_cache_size:
                    self._cleanup_cache()
            except Exception:
                # Silently fail on speculative execution
                pass
        
        return executed
    
    def get_speculative_result(self, pattern: str, context: Dict[str, Any]) -> Optional[Any]:
        """Get a previously speculated result if available."""
        cache_key = f"{pattern}_{hash(str(context))}"
        if cache_key in self._cache:
            age = time.monotonic() - self._cache_timestamps[cache_key]
            if age < self._patterns.get(pattern, SpeculativeTask("", 0, lambda: None, 5.0)).cooldown:
                return self._cache[cache_key]
        return None
    
    def _cleanup_cache(self) -> None:
        """Remove oldest cache entries."""
        sorted_keys = sorted(
            self._cache_timestamps.keys(),
            key=lambda k: self._cache_timestamps[k]
        )
        to_remove = sorted_keys[:len(sorted_keys) // 4]  # Remove 25%
        for key in to_remove:
            del self._cache[key]
            del self._cache_timestamps[key]
    
    def stats(self) -> Dict[str, Any]:
        """Get speculative executor statistics."""
        return {
            "patterns_registered": len(self._patterns),
            "cache_size": len(self._cache),
            "pattern_counts": dict(self._pattern_counts),
            "probabilities": {
                p: t.probability 
                for p, t in self._patterns.items()
            }
        }
