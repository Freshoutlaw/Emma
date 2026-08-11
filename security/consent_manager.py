"""Consent manager — decides when an action needs human approval.

Severities (per action):
    0 LOW      — always allowed, still audited
    1 MED      — allowed in AUTO mode, requires consent in ONCE/STRICT
    2 HIGH     — requires consent unless AUTO or previously approved (ONCE)
    3 CRITICAL — always requires consent (even in AUTO)

Modes:
    auto   — approve everything, log it
    once   — ask the first time per action per session, then remember (default)
    strict — ask every time
"""

from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Optional


# ---------------------------------------------------------------- definitions
# action -> baseline severity. The Guardian may escalate (e.g. a destructive
# shell command) before consulting us.
DEFAULT_RULES: dict[str, int] = {
    # LOW — always allowed
    "read_file": 0,
    "list_dir": 0,
    "system_status": 0,
    "web_search": 0,
    "mqtt_publish": 0,
    "kill_switch": 0,          # emergency stop must never be blocked
    "display_toggle": 0,       # showing/hiding HUD panels
    "consent_mode_change": 0,
    "network_gate_toggle": 0,
    # MED — allowed in auto mode, consent in once/strict
    "run_command": 1,
    "file_write": 1,
    "install_package": 1,
    "network_egress": 1,
    "desktop_control": 1,
    "docker": 1,
    "git_commit": 1,
    "browser_automation": 1,
    # HIGH — consent unless auto or previously approved
    "run_command_destructive": 2,
    "self_modify": 2,
    "git_push": 2,
}

REASONS: dict[str, str] = {
    "read_file": "reading a file",
    "list_dir": "listing a directory",
    "system_status": "reading system status",
    "web_search": "performing a web search",
    "mqtt_publish": "publishing an MQTT message",
    "kill_switch": "toggling the global kill switch",
    "display_toggle": "showing or hiding a HUD panel",
    "consent_mode_change": "changing the consent mode",
    "network_gate_toggle": "toggling the network gate",
    "run_command": "running a shell command",
    "file_write": "writing a file",
    "install_package": "installing a package",
    "network_egress": "opening a network connection",
    "desktop_control": "controlling the desktop",
    "docker": "running a docker command",
    "git_commit": "creating a git commit",
    "browser_automation": "driving a headless browser",
    "run_command_destructive": "running a potentially destructive command",
    "self_modify": "modifying Emma's own code",
    "git_push": "pushing to a git remote",
}


class ConsentMode(str, Enum):
    AUTO = "auto"
    ONCE = "once"
    STRICT = "strict"


@dataclass
class ConsentVerdict:
    allow: bool
    token: Optional[str]
    severity: int
    reason: str


@dataclass
class PendingRequest:
    token: str
    action: str
    reason: str
    severity: int
    created: float
    expires: float

    def to_dict(self) -> dict:
        return {
            "token": self.token,
            "action": self.action,
            "reason": self.reason,
            "severity": self.severity,
            "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.created)),
            "expires": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.expires)),
        }


class ConsentManager:
    def __init__(
        self,
        mode: str | ConsentMode = "once",
        rules: Optional[dict[str, int]] = None,
        approval_ttl: int = 3600,
    ) -> None:
        self.mode = ConsentMode(mode)
        self.rules = dict(DEFAULT_RULES)
        if rules:
            self.rules.update(rules)
        self.approval_ttl = approval_ttl
        self._approved: dict[str, float] = {}  # action -> expiry ts
        self._pending: dict[str, PendingRequest] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ rules
    def severity(self, action: str) -> int:
        return self.rules.get(action, 1)

    def reason(self, action: str) -> str:
        return REASONS.get(action, action.replace("_", " "))

    def set_mode(self, mode: str | ConsentMode) -> None:
        with self._lock:
            self.mode = ConsentMode(mode)

    def revoke(self, action: Optional[str] = None) -> None:
        with self._lock:
            if action:
                self._approved.pop(action, None)
            else:
                self._approved.clear()

    # ------------------------------------------------------------------ decide
    def evaluate(self, action: str, reason: str = "", severity: Optional[int] = None) -> ConsentVerdict:
        """Decide whether the action may proceed.

        `severity` overrides the rules table when the caller (the Guardian)
        has already risk-assessed the specific payload (e.g. a read-only
        shell command is LOW even though `run_command` defaults to MED).
        """
        severity = self.severity(action) if severity is None else severity
        reason = reason or self.reason(action)
        now = time.time()
        with self._lock:
            self._prune(now)
            if severity <= 0:
                return ConsentVerdict(allow=True, token=None, severity=severity, reason=reason)
            if self.mode == ConsentMode.AUTO:
                return ConsentVerdict(allow=True, token=None, severity=severity, reason=reason)
            if self.mode == ConsentMode.ONCE and self._approved.get(action, 0) > now:
                return ConsentVerdict(allow=True, token=None, severity=severity, reason=reason)
        token = secrets.token_urlsafe(16)
        request = PendingRequest(token, action, reason, severity, now, now + 300)
        with self._lock:
            self._pending[token] = request
        return ConsentVerdict(allow=False, token=token, severity=severity, reason=reason)

    # ------------------------------------------------------------------ resolve
    def approve(self, token: str) -> bool:
        with self._lock:
            request = self._pending.pop(token, None)
            if request is None:
                return False
            self._approved[request.action] = time.time() + self.approval_ttl
            return True

    def deny(self, token: str) -> bool:
        with self._lock:
            return self._pending.pop(token, None) is not None

    def pending(self) -> list[dict]:
        with self._lock:
            self._prune(time.time())
            return [r.to_dict() for r in self._pending.values()]

    # ------------------------------------------------------------------ internals
    def _prune(self, now: float) -> None:
        self._approved = {a: t for a, t in self._approved.items() if t > now}
        self._pending = {t: r for t, r in self._pending.items() if r.expires > now}
