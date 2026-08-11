"""Reasoning agent — decomposes requests into tool plans, executes them, and
synthesizes the final answer with the LLM.

Planning is LLM-driven (JSON array of steps) with graceful fallback to plain
chat when no LLM provider is available or the model returns no plan.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import TYPE_CHECKING, Any, AsyncIterator, Optional

from agents.base import AgentResult, BaseAgent
from agents.control import ControlAgent, ToolNotAllowedError
from llm.local import LLMUnavailable
from security.guardian import ConsentRequiredError

if TYPE_CHECKING:
    from agents.router import Pipeline

# Default hard bound on how many steps an agent will execute in one turn
# (the orchestration principle: bound everything). A runaway plan costs one
# LLM call per step and can loop until the model stops emitting steps.
# ReasoningAgent overrides this per-agent (self.max_plan_steps); the system
# prompt advertises it so truncation is rare, and plan() enforces it
# regardless of what the model returns.
MAX_PLAN_STEPS = 10

_JSON_ARRAY_RE = re.compile(r"\[.*\]", re.DOTALL)


def extract_json_array(text: str) -> list[dict]:
    """Pull the first JSON array out of free-form LLM text."""
    if not text:
        return []
    match = _JSON_ARRAY_RE.search(text)
    if not match:
        return []
    try:
        data = json.loads(match.group(0))
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


class ReasoningAgent(BaseAgent):
    name = "reasoning"
    description = "Plans multi-step tasks, executes tools, and synthesizes answers."

    # Least-privilege scoping (orchestration Tier 2): reasoning may plan any
    # tool except the three that irreversibly mutate remote/external state —
    # pushing a branch, tearing down a stack, publishing to an external
    # broker. Those stay on the direct `control` path where the user asks for
    # them explicitly. Everything else remains under the Guardian's severity
    # gates, which is the complementary control (scope vs. severity).
    tool_allowlist: frozenset[str] = frozenset(ControlAgent.TOOL_CATALOG) - frozenset(
        {"git_push", "compose_down", "mqtt_publish"}
    )

    # Explicit per-agent bounds (orchestration Tier 2): the plan loop and
    # each LLM call are capped here, so a runaway plan costs one bounded
    # call per step and stops cleanly instead of spinning.
    max_plan_steps: int = MAX_PLAN_STEPS
    plan_max_tokens: int = 600
    synth_max_tokens: int = 1024
    
    # Cached tool catalog for performance
    _cached_catalog: Optional[dict[str, dict[str, Any]]] = None
    _cached_system_prompt: Optional[str] = None

    # ---------------------------------------------------------------- scoping
    def _catalog(self) -> dict[str, dict[str, Any]]:
        """The subset of the tool catalog this agent may plan against.

        The LLM only ever sees its allowlist — a tool outside it is not
        advertised, so least-privilege is enforced at plan time as well as
        at execute time.
        
        Cached for performance - only computed once per agent instance.
        """
        if self._cached_catalog is None:
            self._cached_catalog = {
                name: spec
                for name, spec in ControlAgent.TOOL_CATALOG.items()
                if name in self.tool_allowlist
            }
        return self._cached_catalog

    def _system_prompt(self) -> str:
        """Generate system prompt with tool catalog.
        
        Cached for performance - only computed once per agent instance.
        """
        if self._cached_system_prompt is None:
            self._cached_system_prompt = (
                "You are Emma, a fully autonomous AI assistant with direct access to the user's system.\n"
                "You plan work as a JSON array of steps. Each step is an object: "
                '{"tool": "<tool name>", "args": {"<arg>": <value>}}.\n'
                "Use ONLY tools from this catalog:\n"
                + json.dumps(self._catalog(), indent=2)
                + '\n\nRules:\n'
                "- If a request needs several actions, chain them as consecutive steps.\n"
                f"- Return at most {self.max_plan_steps} steps; stop planning once the request is fully handled.\n"
                "- If a request needs only reading/knowledge, return an empty array [] and answer directly.\n"
                "- Pass real values; never invent file contents or command output.\n"
                "- Return ONLY the JSON array — no prose, no markdown, no explanation."
                "- Example: [{\"tool\": \"list_dir\", \"args\": {\"path\": \".\"}}]"
            )
        return self._cached_system_prompt

    # ---------------------------------------------------------------- bounds
    def _cap_plan(self, steps: list[dict]) -> list[dict]:
        """Enforce self.max_plan_steps at the source (every caller inherits it).

        Never truncate silently: the audit log keeps the raw count so a capped
        plan is visible in /api/system/activity instead of vanishing.
        """
        if len(steps) <= self.max_plan_steps:
            return steps
        self.pipeline.audit.log(
            "plan.truncated",
            action="reasoning",
            detail={"raw_steps": len(steps), "cap": self.max_plan_steps},
        )
        return steps[: self.max_plan_steps]

    # ---------------------------------------------------------------- planning
    async def plan(self, request: str, context: str = "") -> list[dict]:
        try:
            messages = [
                {"role": "system", "content": self._system_prompt()},
                {
                    "role": "user",
                    "content": f"Context:\n{context or '(none)'}\n\nRequest:\n{request}\n\nPlan now:",
                },
            ]
            text = await asyncio.wait_for(
                self.pipeline.llm.complete(
                    messages, temperature=0.2, max_tokens=self.plan_max_tokens
                ),
                timeout=45,
            )
            return self._cap_plan(extract_json_array(text))
        except (LLMUnavailable, asyncio.TimeoutError):
            return []  # fall back to the keyword planner

    # ---------------------------------------------------------------- fallback
    def _keyword_plan(self, message: str) -> list[dict]:
        """Deterministic tool plan for common verb patterns.

        Used when the LLM returns no plan (weak/slow local models that ramble
        instead of emitting JSON), so common requests still execute tools.
        """
        low = message.lower()
        if "list" in low and any(w in low for w in ("file", "dir", "folder")):
            return [{"tool": "list_dir", "args": {"path": "."}}]
        if "git" in low and "status" in low:
            return [{"tool": "git_status", "args": {}}]
        if "git" in low and "log" in low:
            return [{"tool": "git_log", "args": {}}]
        if "docker" in low and any(w in low for w in ("ps", "container", "running")):
            return [{"tool": "docker_ps", "args": {}}]
        if "docker" in low and "image" in low:
            return [{"tool": "docker_images", "args": {}}]
        for prefix in ("search the web for ", "web search for ", "search for ", "search the web "):
            if low.startswith(prefix):
                return [{"tool": "web_search", "args": {"query": message[len(prefix):].strip() or message}}]
        if "web" in low and "search" in low:
            return [{"tool": "web_search", "args": {"query": message}}]
        return []

    # ---------------------------------------------------------------- execution
    async def run(self, request: str) -> AgentResult:
        context = await self.pipeline.rag.augment(request, k=4)
        steps = await self.plan(request, context)
        if not steps:
            steps = self._keyword_plan(request)
        steps = steps[: self.max_plan_steps]  # defense in depth: bound the loop itself
        actions: list[dict] = []
        outputs: list[str] = []

        for step in steps:
            tool = step.get("tool")
            args = step.get("args", {}) or {}
            if not tool:
                continue
            try:
                output = await self.pipeline.control.execute(tool, actor=self.name, **args)
            except ToolNotAllowedError as exc:
                # Least-privilege enforced at execute time: surface it as data
                # so the synthesized answer stays truthful about what ran.
                self.pipeline.audit.log(
                    "plan.tool_denied", action=tool, actor=f"agent:{self.name}"
                )
                outputs.append(
                    f"[step '{tool}' skipped — not in this agent's tool allowlist]"
                )
                continue
            except ConsentRequiredError as exc:
                return AgentResult(
                    ok=False,
                    output="",
                    intent="reasoning",
                    actions=actions,
                    error="consent required",
                    pending_consent=exc.decision.to_dict(),
                )
            except Exception as exc:  # keep going — one bad step shouldn't kill the task
                outputs.append(f"[step '{tool}' failed: {exc}]")
                continue
            actions.append({"tool": tool, "args": args})
            outputs.append(output)

        final = await self._synthesize(request, context, outputs)
        return AgentResult(ok=True, output=final, intent="reasoning", actions=actions)

    async def _synthesize(self, request: str, context: str, outputs: list[str]) -> str:
        user = (
            f"User request:\n{request}\n\nRelevant context:\n{context or '(none)'}\n\n"
            f"Tool results:\n{json.dumps(outputs, indent=2) if outputs else '(no tools used)'}\n\n"
            "Respond now — concise, direct, and truthful about what was done."
        )
        try:
            return await asyncio.wait_for(
                self.pipeline.llm.complete(
                    [
                        {"role": "system", "content": "You are Emma, a fully autonomous AI assistant. Complete the user's request."},
                        {"role": "user", "content": user},
                    ],
                    temperature=0.6,
                    max_tokens=self.synth_max_tokens,
                ),
                timeout=60,
            )
        except (LLMUnavailable, asyncio.TimeoutError):
            return "\n".join(outputs) if outputs else (
                "⚠ No LLM provider available (start Ollama or set GROQ_API_KEY), and no tools were needed."
            )

    async def stream_narration(self, request: str, context: str, outputs: list[str]) -> AsyncIterator[str]:
        """Stream the synthesized final answer token-by-token."""
        user = (
            f"User request:\n{request}\n\nRelevant context:\n{context or '(none)'}\n\n"
            f"Tool results:\n{json.dumps(outputs, indent=2) if outputs else '(no tools used)'}\n\n"
            "Respond now — concise, direct, and truthful about what was done."
        )
        async for token in self.pipeline.llm.stream(
            [
                {"role": "system", "content": "You are Emma, a fully autonomous AI assistant. Complete the user's request."},
                {"role": "user", "content": user},
            ],
            temperature=0.6,
            max_tokens=self.synth_max_tokens,
        ):
            yield token
