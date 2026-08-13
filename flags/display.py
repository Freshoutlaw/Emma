"""Display state — which HUD panel Emma wants the operator to see right now.

All panels are hidden by default. They appear only when Emma is asked to show
them (voice or text) or when she needs the operator's attention (kill switch
engaged, consent pending). The HUD polls this state and reveals or dismisses
the corresponding overlay panel.

The ``map`` panel carries a ``payload`` describing the region/location Emma
resolved from the request, which the HUD passes to the map dashboard.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Optional

PANELS = ("memory", "status", "guardian", "map", "board")


class DisplayState:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._panel: Optional[str] = None
        self._reason: str = ""
        self._payload: Optional[dict] = None
        self._ts: str = ""

    @staticmethod
    def _now() -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    def set(self, panel: Optional[str], reason: str = "", payload: Optional[dict] = None) -> dict:
        if panel is not None and panel not in PANELS:
            raise ValueError(f"unknown panel '{panel}'; expected one of {PANELS}")
        # Note: build the state dict directly — calling state() here would
        # deadlock, because threading.Lock is not reentrant.
        with self._lock:
            self._panel = panel
            self._reason = reason
            self._payload = payload
            self._ts = self._now()
            return {
                "panel": self._panel,
                "reason": self._reason,
                "payload": self._payload,
                "ts": self._ts,
            }

    def clear(self, reason: str = "operator dismissed") -> dict:
        return self.set(None, reason)

    def state(self) -> dict:
        with self._lock:
            return {
                "panel": self._panel,
                "reason": self._reason,
                "payload": self._payload,
                "ts": self._ts,
            }
