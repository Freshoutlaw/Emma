"""Security agent — kill switch, consent mode, network gate, status reports.

Status reports and kill-switch changes also push the guardian panel to the
HUD, because that is exactly when the operator needs to see it.
"""

from __future__ import annotations

import re

from agents.base import AgentResult, BaseAgent

_KILL_ON_WORDS = ("engage", "arm", "activate", "enable", "on", "stop")
_KILL_OFF_WORDS = ("disengage", "release", "reset", "off", "deactivate", "disable")
_HIDE_WORDS = ("hide", "close", "dismiss", "clear")


def _has_word(text: str, words: tuple) -> bool:
    """True if any word appears as a whole word (so 'disengage' never matches 'engage')."""
    return any(re.search(r"\b" + re.escape(w) + r"\b", text) for w in words)


class SecurityAgent(BaseAgent):
    name = "security"
    description = "Manages the kill switch, consent mode, network gate, security status and HUD panels."

    async def status(self) -> dict:
        p = self.pipeline
        return {
            "kill_switch": p.kill_switch.is_engaged(),
            "kill_switch_reason": p.kill_switch.reason(),
            "consent_mode": p.consent.mode.value,
            "pending_consent": p.consent.pending(),
            "network_gate": p.network_gate.is_open,
            "llm_route": p.llm.route(),
            "memory_episodes": p.episodic.count(),
        }

    # ---------------------------------------------------------------- run
    async def run(self, request: str) -> AgentResult:
        low = request.lower()
        p = self.pipeline

        if "panel" in low or "display" in low:
            if any(w in low for w in _HIDE_WORDS):
                p.display.clear(reason="operator request")
                return AgentResult(ok=True, output="Panels hidden. The view is clear again.", intent="security")

        if "kill" in low and _has_word(low, _KILL_ON_WORDS):
            p.kill_switch.engage(reason="operator request via agent")
            p.display.set("guardian", reason="kill switch engaged — action required")
            self._audit("kill_switch.engaged", action="kill_switch", detail={"via": "agent"})
            return AgentResult(
                ok=True,
                output="Kill switch ENGAGED. All command execution and file modification is now blocked.",
                intent="security",
            )

        if "kill" in low and _has_word(low, _KILL_OFF_WORDS):
            p.kill_switch.disengage()
            p.display.set("guardian", reason="kill switch disengaged")
            self._audit("kill_switch.disengaged", action="kill_switch", detail={"via": "agent"})
            return AgentResult(ok=True, output="Kill switch DISENGAGED. Emma is operational again.", intent="security")

        if "consent" in low and "mode" in low:
            mode = next((m for m in ("auto", "once", "strict") if m in low), None)
            if mode:
                p.consent.set_mode(mode)
                self._audit("consent.mode_changed", action="consent_mode_change", detail={"mode": mode})
                return AgentResult(ok=True, output=f"Consent mode set to '{mode}'.", intent="security")

        if "network" in low and any(w in low for w in ("open", "allow", "enable")):
            p.network_gate.open(reason="operator request via agent")
            p.display.set("guardian", reason="network gate opened")
            return AgentResult(ok=True, output="Network gate OPEN — outbound calls allowed.", intent="security")

        if "network" in low and any(w in low for w in ("close", "block", "disable")):
            p.network_gate.close(reason="operator request via agent")
            p.display.set("guardian", reason="network gate closed")
            return AgentResult(ok=True, output="Network gate CLOSED — outbound calls blocked.", intent="security")

        # Status report → push the relevant panel so the operator sees it.
        if any(w in low for w in ("guardian", "security", "kill", "consent", "network")):
            p.display.set("guardian", reason="operator request")
        else:
            p.display.set("status", reason="operator request")
        status = await self.status()
        lines = [f"{key}: {value}" for key, value in status.items()]
        return AgentResult(ok=True, output="Security status:\n" + "\n".join(lines), intent="security")
