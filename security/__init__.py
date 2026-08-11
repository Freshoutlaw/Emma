"""Emma security core — guardian, consent, audit, encryption and kill switch.

The Guardian is the single policy enforcement point for every action Emma
performs. It classifies risk, consults the consent manager, and records every
verdict in the append-only audit log. A global kill switch can halt all
command execution instantly.
"""

from security.audit_log import AuditLog
from security.consent_manager import (
    ConsentManager,
    ConsentMode,
    ConsentVerdict,
    DEFAULT_RULES,
    PendingRequest,
)
from security.encryption import SecretBox
from security.guardian import ConsentRequiredError, Decision, Guardian
from security.kill_switch import KillSwitch

__all__ = [
    "AuditLog",
    "ConsentManager",
    "ConsentMode",
    "ConsentVerdict",
    "DEFAULT_RULES",
    "PendingRequest",
    "SecretBox",
    "ConsentRequiredError",
    "Decision",
    "Guardian",
    "KillSwitch",
]
