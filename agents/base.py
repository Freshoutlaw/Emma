"""Base agent contract — every Emma agent produces an AgentResult."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from orchestration.handoff import HandoffProposal

if TYPE_CHECKING:
    from agents.router import Pipeline


@dataclass
class AgentResult:
    ok: bool
    output: str
    intent: str = "chat"
    actions: list[dict] = field(default_factory=list)
    memory_ids: list[str] = field(default_factory=list)
    error: Optional[str] = None
    pending_consent: Optional[dict] = None
    # Tier 5: optional handoff proposal attached to the result.
    handoff: Optional["HandoffProposal"] = None

    def to_dict(self) -> dict:
        d = {
            "ok": self.ok,
            "output": self.output,
            "intent": self.intent,
            "actions": self.actions,
            "memory_ids": self.memory_ids,
            "error": self.error,
            "pending_consent": self.pending_consent,
        }
        if self.handoff:
            d["handoff"] = self.handoff.to_dict()
        return d


class BaseAgent:
    name: str = "base"
    description: str = "Base agent."

    # Least-privilege tool scoping (orchestration Tier 2). An optional
    # frozenset of tool names from ControlAgent.TOOL_CATALOG that this agent
    # may call; None means the full catalog. ControlAgent.execute() enforces
    # it at call time, so an agent can never run a tool outside its allowlist
    # even if the LLM plans one.
    tool_allowlist: Optional[frozenset[str]] = None

    def __init__(self, pipeline: "Pipeline") -> None:
        self.pipeline = pipeline

    async def run(self, request: str) -> AgentResult:
        raise NotImplementedError

    # ---------------------------------------------------------------- helpers
    def _audit(self, event: str, **kwargs: Any) -> None:
        self.pipeline.audit.log(event, actor=f"agent:{self.name}", **kwargs)
