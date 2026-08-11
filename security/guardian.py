"""The Guardian — central policy enforcement for every action Emma performs.

Every capability goes through `Guardian.guard(action, payload)` before doing
anything. The guardian:

1. Checks the global kill switch first — engaged means everything is denied.
2. Classifies risk (shell commands get pattern-based escalation, file writes
   get path-based escalation).
3. Consults the consent manager, which either allows the action or issues a
   consent token.
4. Records every verdict in the append-only audit log.

When consent is required, `guard()` raises `ConsentRequiredError` carrying the
`Decision` — the API layer turns that into an HTTP 409 with an approval token.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from security.audit_log import AuditLog
from security.consent_manager import ConsentManager
from security.kill_switch import KillSwitch

SEVERITY_LABELS = {0: "LOW", 1: "MED", 2: "HIGH", 3: "CRITICAL"}


@dataclass
class Decision:
    allow: bool
    action: str
    severity: int
    reason: str
    token: Optional[str] = None
    require_consent: bool = False

    def to_dict(self) -> dict:
        return {
            "allow": self.allow,
            "action": self.action,
            "severity": SEVERITY_LABELS.get(self.severity, "LOW"),
            "reason": self.reason,
            "token": self.token,
            "require_consent": self.require_consent,
        }


class ConsentRequiredError(Exception):
    """Raised by the Guardian when an action awaits operator approval."""

    def __init__(self, decision: Decision) -> None:
        super().__init__(f"consent required for {decision.action}: {decision.reason}")
        self.decision = decision


# Patterns that escalate a shell command to HIGH severity.
DESTRUCTIVE_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\brm\s+-rf?\b", re.I), "recursive force delete"),
    (re.compile(r"\bmkfs\.?\w*", re.I), "filesystem format"),
    (re.compile(r"\bdd\b[^\n]*\bof=/dev/", re.I), "raw block device write"),
    (re.compile(r">\s*/dev/sd", re.I), "raw block device write"),
    (re.compile(r"\bgit\s+push\b", re.I), "git push to remote"),
    (re.compile(r"\bdocker\s+(rm|rmi|system\s+prune|volume\s+rm|network\s+rm)\b", re.I), "destructive docker command"),
    (re.compile(r"\bDROP\s+TABLE\b|\bTRUNCATE\b", re.I), "destructive database command"),
    (re.compile(r"\bcurl\b[^\n]*\|\s*(ba|z)?sh\b", re.I), "pipe-to-shell"),
    (re.compile(r"\b(shutdown|reboot|poweroff|halt)\b", re.I), "system shutdown"),
    (re.compile(r"\bsudo\b", re.I), "privilege escalation"),
    (re.compile(r"\bkill\s+-9\b|\bpkill\b|\bkillall\b", re.I), "force kill"),
    (re.compile(r"\b(systemctl|service)\s+[^\s]+\s+(stop|restart|disable)\b", re.I), "service control"),
]

# Prefixes that mark a shell command as read-only (severity LOW).
READONLY_PREFIX = re.compile(
    r"^(ls|cat|pwd|echo|whoami|date|df|du|free|uname|hostname|env|which|head|tail|grep|find\b[^\n]*(-delete|-exec\b)?|"
    r"git\s+(status|diff|log|branch)|docker\s+(ps|images|logs)|docker\s+compose\s+ps|pip\s+list|"
    r"ps\s+aux|top\s+-b|python3?\s+-V|python\s+-V)",
    re.IGNORECASE,
)

# Paths that escalate file writes to HIGH severity.
SENSITIVE_PATH_PARTS = ("/etc/", "/usr/", "/bin/", "/sbin/", "/boot/", "/dev/", "/root/", "/proc/", "/sys/")
SENSITIVE_NAME_PARTS = (".ssh", ".env", ".aws", ".gnupg", "shadow", "passwd")


class Guardian:
    def __init__(self, consent: ConsentManager, audit: AuditLog, kill_switch: KillSwitch) -> None:
        self.consent = consent
        self.audit = audit
        self.kill_switch = kill_switch

    # ---------------------------------------------------------------- risk
    def assess_command(self, command: str) -> tuple[int, str]:
        """Return (severity, reason) for a shell command string."""
        cmd = (command or "").strip()
        if not cmd:
            return 1, "empty shell command"
        if READONLY_PREFIX.match(cmd):
            return 0, "read-only shell command"
        for pattern, why in DESTRUCTIVE_PATTERNS:
            if pattern.search(cmd):
                return 2, why
        return 1, "shell command"

    def assess_file_write(self, path: str) -> tuple[int, str]:
        low = path.lower()
        if any(part in low for part in SENSITIVE_PATH_PARTS) or any(part in low for part in SENSITIVE_NAME_PARTS):
            return 2, "write to sensitive system path"
        return self.consent.severity("file_write"), "file write"

    def _severity(self, action: str, payload: Optional[dict]) -> tuple[int, str]:
        payload = payload or {}
        if action == "run_command":
            return self.assess_command(str(payload.get("command", "")))
        if action == "file_write":
            return self.assess_file_write(str(payload.get("path", "")))
        if action == "run_command_destructive":
            return 2, "potentially destructive shell command"
        return self.consent.severity(action), self.consent.reason(action)

    # ---------------------------------------------------------------- guard
    def guard(self, action: str, payload: Optional[dict] = None, actor: str = "agent") -> Decision:
        if self.kill_switch.is_engaged():
            decision = Decision(
                allow=False,
                action=action,
                severity=3,
                reason=f"kill switch engaged ({self.kill_switch.reason() or 'operator'})",
            )
            self.audit.log("guard.denied", action=action, actor=actor, decision=decision.to_dict(), detail=payload)
            return decision

        severity, reason = self._severity(action, payload)
        verdict = self.consent.evaluate(action, reason, severity=severity)
        if verdict.allow:
            decision = Decision(allow=True, action=action, severity=severity, reason=reason)
            self.audit.log("guard.allowed", action=action, actor=actor, decision=decision.to_dict(), detail=payload)
            return decision

        decision = Decision(
            allow=False,
            action=action,
            severity=severity,
            reason=reason,
            token=verdict.token,
            require_consent=True,
        )
        self.audit.log(
            "guard.pending_consent",
            action=action,
            actor=actor,
            decision=decision.to_dict(),
            detail=payload,
        )
        raise ConsentRequiredError(decision)
