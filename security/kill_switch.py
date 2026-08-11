"""Global kill switch.

When engaged, the Guardian denies every action with severity CRITICAL and
reason "kill switch engaged". It is file-backed so the state survives restarts
and is shared across processes. Engaging it is always allowed (severity LOW) —
it is Emma's emergency stop, and it must never be gated behind consent.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional


class KillSwitch:
    def __init__(self, state_file: str | Path = "data/kill_switch") -> None:
        self.path = Path(state_file)

    def engage(self, reason: str = "operator") -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().astimezone().isoformat(timespec="seconds")
        self.path.write_text(f"{ts} {reason}", encoding="utf-8")

    def disengage(self) -> None:
        self.path.unlink(missing_ok=True)

    def is_engaged(self) -> bool:
        return self.path.exists()

    def reason(self) -> Optional[str]:
        try:
            text = self.path.read_text(encoding="utf-8").strip()
            if not text:
                return None
            # Format: "<iso ts> <reason>"
            return text.split(" ", 1)[1] if " " in text else "engaged"
        except FileNotFoundError:
            return None
        except Exception:
            return "engaged"
