"""Agent Pool — Pre-warmed agent instances for faster initialization.

OPTIMIZATIONS:
- Agent instance pooling to avoid repeated initialization
- Lazy loading of agents
- Configurable pool size
"""

from __future__ import annotations

from typing import Dict, Optional, Type
from queue import Queue
import threading

from agents.base import BaseAgent, AgentResult


class AgentPool:
    """Pool of pre-warmed agent instances for faster initialization."""
    
    def __init__(self, max_pool_size: int = 5) -> None:
        self.max_pool_size = max_pool_size
        self._pools: Dict[str, Queue[BaseAgent]] = {}
        self._lock = threading.Lock()
    
    def get_agent(self, agent_class: Type[BaseAgent], pipeline, agent_name: str) -> BaseAgent:
        """Get an agent from the pool or create a new one."""
        with self._lock:
            if agent_name not in self._pools:
                self._pools[agent_name] = Queue(maxsize=self.max_pool_size)
            
            pool = self._pools[agent_name]
            
            # Try to get from pool
            if not pool.empty():
                return pool.get()
            
            # Create new instance if pool is empty
            return agent_class(pipeline)
    
    def return_agent(self, agent: BaseAgent, agent_name: str) -> None:
        """Return an agent to the pool for reuse."""
        with self._lock:
            if agent_name not in self._pools:
                self._pools[agent_name] = Queue(maxsize=self.max_pool_size)
            
            pool = self._pools[agent_name]
            
            # Only return if pool is not full
            if pool.qsize() < self.max_pool_size:
                pool.put(agent)
    
    def clear(self) -> None:
        """Clear all agent pools."""
        with self._lock:
            self._pools.clear()


# Global agent pool instance
agent_pool = AgentPool()
