"""Append-only audit log (JSON Lines) with automatic rotation.

Every guardian verdict, agent action, HTTP request and self-modification is
recorded here. Logs are never rewritten — rotation simply renames the current
file to `<name>.1` and starts a fresh one.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class AuditLog:
    """Thread-safe JSONL audit log."""

    def __init__(self, path: str | Path = "data/audit.log", max_entries: int = 5000) -> None:
        self.path = Path(path)
        self.max_entries = max_entries
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._count = self._count_lines()

    # ------------------------------------------------------------------ write
    def _count_lines(self) -> int:
        try:
            if not self.path.exists():
                return 0
            with self.path.open(encoding="utf-8") as fh:
                return sum(1 for _ in fh)
        except Exception:
            return 0

    def log(
        self,
        event: str,
        *,
        action: Optional[str] = None,
        actor: str = "system",
        decision: Optional[dict] = None,
        detail: Any = None,
    ) -> dict:
        entry = {
            "ts": _now(),
            "event": event,
            "action": action,
            "actor": actor,
            "decision": decision,
            "detail": detail,
        }
        with self._lock:
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, default=str) + "\n")
            self._count += 1
            if self._count >= self.max_entries:
                self._rotate()
        return entry

    def _rotate(self) -> None:
        try:
            backup = self.path.with_suffix(self.path.suffix + ".1")
            backup.write_bytes(self.path.read_bytes())
            self.path.unlink(missing_ok=True)
            self._count = 0
        except Exception:
            # Rotation is best-effort; never crash the caller over logs.
            pass

    # ------------------------------------------------------------------ read
    def recent(self, limit: int = 50) -> list[dict]:
        if not self.path.exists():
            return []
        lines = self.path.read_text(encoding="utf-8").splitlines()
        out: list[dict] = []
        for line in lines[-limit:]:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out
