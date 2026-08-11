"""Network egress gate — a single switch that can block all outbound network calls.

Emma's web search, browser automation and page fetches all consult this gate
before touching the internet. It is file-backed so the state is shared across
processes (e.g. the API server and any agent worker).
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Optional


class NetworkGateState:
    """Serializable state persisted to disk."""

    def __init__(self, open_: bool, reason: str = "", ts: Optional[str] = None) -> None:
        self.open = open_
        self.reason = reason
        self.ts = ts or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    def to_dict(self) -> dict:
        return {"open": self.open, "reason": self.reason, "ts": self.ts}


class NetworkGate:
    """File-backed on/off switch for outbound network access."""

    def __init__(self, state_file: Optional[str | Path] = None, default_open: bool = True) -> None:
        self._state_file = Path(state_file) if state_file else None
        self._lock = threading.Lock()
        self._state = NetworkGateState(open_=default_open)
        if self._state_file:
            self._load()

    # ------------------------------------------------------------------ state
    def _load(self) -> None:
        try:
            if self._state_file.exists():
                data = json.loads(self._state_file.read_text(encoding="utf-8"))
                self._state = NetworkGateState(open_=bool(data.get("open", True)), reason=data.get("reason", ""), ts=data.get("ts"))
        except Exception:
            # Corrupt state file — default to open and let the operator decide.
            self._state = NetworkGateState(open_=True, reason="state file unreadable, reset to open")

    def _save(self) -> None:
        if not self._state_file:
            return
        self._state_file.parent.mkdir(parents=True, exist_ok=True)
        self._state_file.write_text(json.dumps(self._state.to_dict()), encoding="utf-8")

    # ------------------------------------------------------------------ api
    @property
    def is_open(self) -> bool:
        with self._lock:
            return self._state.open

    @property
    def state(self) -> dict:
        with self._lock:
            return self._state.to_dict()

    def open(self, reason: str = "operator") -> None:
        with self._lock:
            self._state = NetworkGateState(open_=True, reason=reason)
            self._save()

    def close(self, reason: str = "operator") -> None:
        with self._lock:
            self._state = NetworkGateState(open_=False, reason=reason)
            self._save()

    def set(self, open_: bool, reason: str = "operator") -> NetworkGateState:
        with self._lock:
            self._state = NetworkGateState(open_=open_, reason=reason)
            self._save()
            return self._state

    def __enter__(self) -> "NetworkGate":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def __repr__(self) -> str:  # pragma: no cover
        return f"<NetworkGate open={self.is_open}>"
