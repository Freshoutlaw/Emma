"""Read-only observer bus for the Living Mind (/mind) visualization.

A spectator, not a participant: observers only ever RECEIVE. The bus keeps
a set of observer WebSockets and fans each event out to all of them.

Two rules from the design brief, enforced here:

- Every send is wrapped in a ~1s timeout and dead sockets are pruned, so a
  sleeping tab with a half-open connection can never block the `await` that
  a real user's turn is sitting behind.
- This module never touches the main UI / chat / voice connection state.
  It is a completely separate channel.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

log = logging.getLogger(__name__)

SEND_TIMEOUT_S = 1.0


class MindBus:
    """Fan-out hub for mind events. Sends are timeout-guarded and pruned."""

    def __init__(self) -> None:
        self._observers: set[Any] = set()
        self._lock = asyncio.Lock()

    async def register(self, websocket: Any) -> None:
        async with self._lock:
            self._observers.add(websocket)

    async def unregister(self, websocket: Any) -> None:
        async with self._lock:
            self._observers.discard(websocket)

    def observer_count(self) -> int:
        return len(self._observers)

    async def publish(self, event: dict) -> None:
        """Fan an event out to every observer. Never raises, never blocks
        the caller past one timeout per socket."""
        if not self._observers:
            return
        payload = dict(event)
        payload.setdefault("ts", datetime.now(timezone.utc).isoformat(timespec="seconds"))
        text = json.dumps(payload, default=str)
        async with self._lock:
            observers = list(self._observers)
        dead: list[Any] = []
        for ws in observers:
            try:
                await asyncio.wait_for(ws.send_text(text), timeout=SEND_TIMEOUT_S)
            except Exception:
                dead.append(ws)
        if dead:
            async with self._lock:
                for ws in dead:
                    self._observers.discard(ws)


# Process-wide singleton — the emit points (memory recall/write, dispatch,
# turn completion) import this directly so the map sees what really happens.
mind_bus = MindBus()
