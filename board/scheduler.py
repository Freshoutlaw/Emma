"""Standing review (Tier 7) — the board shows up when you didn't call the
meeting.

Runs only where the agent actually stays alive (the backend lifespan). Once
a day it checks whether a month has passed since the last unprompted
standing review; if so it convenes the meeting with the mandated prompt and
no question of the operator's choosing.

The prompt's last line is load-bearing: the board must NOT hand the agenda
back. It is marked unprompted so the operator can tell it apart from an
answer they asked for.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

log = logging.getLogger(__name__)

STANDING_REVIEW_INTERVAL_DAYS = 30
STANDING_REVIEW_QUESTION = (
    "You did not call this meeting, so there is no question but the business. "
    "Read the numbers. What would you put on the agenda this month that they are "
    "not already looking at? Name the specific number that moves you, say what it "
    "implies, and give one concrete thing to do in the next 30 days. If the numbers "
    "genuinely warrant nothing, say so plainly rather than manufacturing a concern. "
    "Do not ask what they want to discuss — this is your agenda, not theirs."
)


class BoardScheduler:
    def __init__(self, convene: Any, store: Any, interval_seconds: int = 3600 * 24) -> None:
        """``convene`` is an async callable(question, prompted=False) -> result."""
        self._convene = convene
        self._store = store
        self._interval = interval_seconds
        self._task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._loop(), name="board-standing-review")
        log.info("board scheduler started (interval=%ss)", self._interval)

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except (asyncio.CancelledError, Exception):
            pass
        self._task = None

    def due(self, now: Optional[datetime] = None) -> bool:
        now = now or datetime.now(timezone.utc)
        last = self._store.last_standing_review()
        if last is None:
            return True  # never convened — the standing review should exist
        try:
            last_dt = datetime.fromisoformat(last)
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=timezone.utc)
        except ValueError:
            return True
        return (now - last_dt) >= timedelta(days=STANDING_REVIEW_INTERVAL_DAYS)

    async def _loop(self) -> None:
        while True:
            try:
                if self.due():
                    log.info("board standing review due — convening unprompted meeting")
                    try:
                        await self._convene(STANDING_REVIEW_QUESTION, prompted=False)
                    except Exception:
                        log.exception("board standing review failed")
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("board scheduler iteration failed")
            await asyncio.sleep(self._interval)
