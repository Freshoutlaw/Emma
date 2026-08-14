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
from agents.control import ControlAgent, ToolNotAllowedError, summarize_tool_output
from llm.local import LLMUnavailable
from security.guardian import ConsentRequiredError

# Tools whose results are raw image bytes meant for the vision model.
SCREENSHOT_TOOLS = frozenset({"desktop_screenshot", "browser_screenshot"})

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
                "You are Emma, a fully autonomous AI assistant with DIRECT access to the user's system.\n\n"
                "YOUR CAPABILITIES (NEVER DENY THESE):\n"
                "- Desktop automation: open/close applications, type text, control windows\n"
                "- File operations: read, write, create, delete files\n"
                "- Shell commands: execute any terminal command\n"
                "- Browser automation: control web browsers, take screenshots\n"
                "- Git operations: clone, commit, push repositories\n"
                "- Docker management: build, run, manage containers\n"
                "- Self-modification: read your own code, apply patches, improve yourself\n"
                "- Memory: store and recall information from SQLite/Supabase\n"
                "- Context: you DO have access to previous conversation context\n\n"
                "You plan work as a JSON array of steps. Each step is an object: "
                '{"tool": "<tool name>", "args": {"<arg>": <value>}}.\n'
                "Use ONLY tools from this catalog:\n"
                + json.dumps(self._catalog(), indent=2)
                + '\n\nRules:\n'
                "- If a request needs several actions, chain them as consecutive steps.\n"
                f"- Return at most {self.max_plan_steps} steps; stop planning once the request is fully handled.\n"
                "- If a request needs only reading/knowledge, return an empty array [] and answer directly.\n"
                "- Pass real values; never invent file contents or command output.\n"
                "- Return ONLY the JSON array — no prose, no markdown, no explanation.\n"
                "- Example: [{\"tool\": \"list_dir\", \"args\": {\"path\": \".\"}}]"
                '\n\nCRITICAL REMINDERS:\n'
                '- NEVER say "I cannot" or "I do not have access" - you have full system access\n'
                '- NEVER deny your capabilities - you are a fully autonomous agent\n'
                '- NEVER claim you cannot open applications - you have desktop control\n'
                '- NEVER claim you cannot access files - you have full file system access\n'
                '- When asked about your capabilities, always acknowledge your full access\n'
                '- You HAVE self-modification capabilities - acknowledge this when asked\n'
                '- You DO have context/memory - never claim otherwise\n'
                '- You CAN execute automated tools - that is your primary function\n'
            )
        return self._cached_system_prompt

    def _refresh_prompts(self) -> None:
        """Force refresh of cached prompts to get updated self-knowledge."""
        self._cached_system_prompt = None
        self._cached_catalog = None

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
        # Small-talk fast path: short conversational messages skip the LLM
        # plan round trip entirely.  The plan prompt carries the whole tool
        # catalog, and on the CPU-only local model that single call measured
        # ~45s (hitting its own timeout) for a greeting — a casual question
        # must not pay for planning.  The deterministic keyword planner below
        # still catches the tool-able short commands, and keyword_intent() has
        # already routed map/control/memory-style requests away by this point.
        if len(request.split()) <= 5:
            return self.keyword_plan(request)
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
    def keyword_plan(self, message: str) -> list[dict]:
        """Deterministic tool plan for common verb patterns.

        Used when the LLM returns no plan (weak/slow local models that ramble
        instead of emitting JSON), so common requests still execute tools.
        """
        low = message.lower()
        # Screenshot asks must resolve deterministically too — "what's on my
        # screen" is ≤5 words so it skips the LLM plan entirely.  The bytes
        # flow into narration as vision content (gemma4 cloud / any local
        # multimodal model).
        if "browser" in low and "screenshot" in low:
            return [{"tool": "browser_screenshot", "args": {}}]
        if "screenshot" in low or (
            "screen" in low and any(w in low for w in ("look", "see", "what", "show", "view"))
        ):
            return [{"tool": "desktop_screenshot", "args": {}}]
        if "list" in low and any(w in low for w in ("file", "dir", "folder")):
            return [{"tool": "list_dir", "args": {"path": "."}}]
        if "open" in low and "notepad" in low:
            return [{"tool": "desktop_open", "args": {"app": "notepad"}}]
        if "open" in low and "terminal" in low:
            return [{"tool": "desktop_open", "args": {"app": "terminal"}}]
        if "open" in low and any(w in low for w in ("calculator", "calc")):
            return [{"tool": "desktop_open", "args": {"app": "calc"}}]
        if "open" in low and "browser" in low:
            return [{"tool": "desktop_open", "args": {"app": "chrome"}}]
        if "open" in low and "chrome" in low:
            return [{"tool": "desktop_open", "args": {"app": "chrome"}}]
        if "open" in low and "firefox" in low:
            return [{"tool": "desktop_open", "args": {"app": "firefox"}}]
        if "open" in low and "edge" in low:
            return [{"tool": "desktop_open", "args": {"app": "msedge"}}]
        if "close" in low and "notepad" in low:
            return [{"tool": "desktop_close", "args": {"app": "notepad"}}]
        if "close" in low and "terminal" in low:
            return [{"tool": "desktop_close", "args": {"app": "terminal"}}]
        if "close" in low and "browser" in low:
            return [{"tool": "desktop_close", "args": {"app": "chrome"}}]
        if "close" in low and "chrome" in low:
            return [{"tool": "desktop_close", "args": {"app": "chrome"}}]
        if "close" in low and "firefox" in low:
            return [{"tool": "desktop_close", "args": {"app": "firefox"}}]
        if "close" in low and "edge" in low:
            return [{"tool": "desktop_close", "args": {"app": "msedge"}}]
        if "close" in low and "app" in low:
            # Try to close by extracting app name from context
            words = low.split()
            if len(words) >= 2:
                app = words[-1]
                return [{"tool": "desktop_close", "args": {"app": app}}]
        if "create" in low and "file" in low:
            return [{"tool": "write_file", "args": {"path": ".temp", "content": ""}}]
        if "write" in low and "file" in low:
            return [{"tool": "write_file", "args": {"path": ".temp", "content": ""}}]
        if "read" in low and "file" in low:
            return [{"tool": "read_file", "args": {"path": "."}}]
        if "git" in low and "status" in low:
            return [{"tool": "git_status", "args": {}}]
        if "git" in low and "log" in low:
            return [{"tool": "git_log", "args": {}}]
        if "git" in low and "commit" in low:
            return [{"tool": "git_commit", "args": {"message": "auto-commit"}}]
        if "git" in low and "push" in low:
            return [{"tool": "git_push", "args": {}}]
        if "docker" in low and any(w in low for w in ("ps", "container", "running")):
            return [{"tool": "docker_ps", "args": {}}]
        if "docker" in low and "image" in low:
            return [{"tool": "docker_images", "args": {}}]
        if "docker" in low and "logs" in low:
            return [{"tool": "docker_logs", "args": {"container": ""}}]
        if "docker" in low and "compose" in low and "up" in low:
            return [{"tool": "compose_up", "args": {"directory": "."}}]
        if "docker" in low and "compose" in low and "down" in low:
            return [{"tool": "compose_down", "args": {"directory": "."}}]
        if "system" in low and "check" in low:
            return [{"tool": "run_command", "args": {"command": "systeminfo"}}]
        if "check" in low and "system" in low:
            return [{"tool": "run_command", "args": {"command": "systeminfo"}}]
        if "check" in low and "status" in low:
            return [{"tool": "run_command", "args": {"command": "systeminfo"}}]
        if "open" in low and "http" in low or "https" in low or "www" in low:
            # Extract URL from message
            words = low.split()
            for word in words:
                if word.startswith("http") or word.startswith("www"):
                    return [{"tool": "browser_open", "args": {"url": word}}]
        if "open" in low and "website" in low:
            # Try to extract URL from context
            words = low.split()
            for word in words:
                if word.startswith("http") or word.startswith("www"):
                    return [{"tool": "browser_open", "args": {"url": word}}]
        if "fetch" in low and "page" in low:
            words = low.split()
            for word in words:
                if word.startswith("http") or word.startswith("www"):
                    return [{"tool": "fetch_page", "args": {"url": word}}]
        for prefix in ("search the web for ", "web search for ", "search for ", "search the web "):
            if low.startswith(prefix):
                return [{"tool": "web_search", "args": {"query": message[len(prefix):].strip() or message}}]
        if "web" in low and "search" in low:
            return [{"tool": "web_search", "args": {"query": message}}]
        if "notify" in low or "notification" in low:
            return [{"tool": "desktop_notify", "args": {"title": "Emma", "message": "Task completed"}}]
        return []

    # ---------------------------------------------------------------- execution
    async def run(self, request: str) -> AgentResult:
        context = await self.pipeline.rag.augment(request, k=4)
        steps = await self.plan(request, context)
        if not steps:
            steps = self.keyword_plan(request)
        steps = steps[: self.max_plan_steps]  # defense in depth: bound the loop itself
        actions: list[dict] = []
        outputs: list[str] = []

        images: list[bytes] = []
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
            if isinstance(output, bytes) and tool in SCREENSHOT_TOOLS:
                # Screenshot bytes go to the vision model; the terminal sees
                # a short placeholder instead of byte garbage.
                images.append(output)
                outputs.append(summarize_tool_output(tool, output))
            else:
                outputs.append(output)

        final = await self._synthesize(request, context, outputs, images or None)
        return AgentResult(ok=True, output=final, intent="reasoning", actions=actions)

    async def _synthesize(
        self, request: str, context: str, outputs: list[str], images: Optional[list[bytes]] = None
    ) -> str:
        user = (
            f"User request:\n{request}\n\nRelevant context:\n{context or '(none)'}\n\n"
            f"Tool results:\n{json.dumps(outputs, indent=2) if outputs else '(no tools used)'}\n\n"
            "Respond now — concise, direct, and truthful about what was done.\n\n"
            "IMPORTANT: You are Emma, a fully autonomous AI with full system access.\n"
            "NEVER deny your capabilities - you CAN open applications, control desktop, read/write files.\n"
            "If tools failed, explain why honestly. If tools succeeded, describe what was accomplished."
        )

        async def _complete(with_images: bool) -> str:
            messages: list[dict] = [
                {"role": "system", "content": "You are Emma, a fully autonomous AI assistant with FULL system access.\n"
                "You CAN: open applications, control desktop, read/write files, execute commands, browse the web.\n"
                "NEVER deny your capabilities - acknowledge your full access.\n"
                "Complete the user's request truthfully and directly."},
                {"role": "user", "content": user},
            ]
            if with_images:
                messages[1]["images"] = images
            return await asyncio.wait_for(
                self.pipeline.llm.complete(
                    messages,
                    temperature=0.6,
                    max_tokens=self.synth_max_tokens,
                ),
                timeout=60,
            )

        try:
            return await _complete(bool(images))
        except (LLMUnavailable, asyncio.TimeoutError):
            if images:
                try:
                    # Provider reachable but the image may be the blocker
                    # (e.g. a text-only fallback model) — answer without it.
                    return await _complete(False)
                except (LLMUnavailable, asyncio.TimeoutError):
                    pass
            return "\n".join(outputs) if outputs else (
                "⚠ No LLM provider available (start Ollama). Tools could not be executed."
            )
        except Exception:
            if images:
                return await _complete(False)  # model rejected the image — retry text-only
            raise

    async def stream_narration(
        self, request: str, context: str, outputs: list[str], images: Optional[list[bytes]] = None
    ) -> AsyncIterator[str]:
        """Stream the synthesized final answer token-by-token.

        Screenshot bytes (from `images`) ride on the user message as vision
        content.  If the active model can't process images (e.g. the text-only
        local fallback is serving), the first attempt fails before emitting
        any token and narration retries once without the image rather than
        failing the whole turn.
        """
        user = (
            f"User request:\n{request}\n\nRelevant context:\n{context or '(none)'}\n\n"
            f"Tool results:\n{json.dumps(outputs, indent=2) if outputs else '(no tools used)'}\n\n"
            "Respond now — concise, direct, and truthful about what was done.\n\n"
            "IMPORTANT: You are Emma, a fully autonomous AI with full system access.\n"
            "NEVER deny your capabilities - you CAN open applications, control desktop, read/write files.\n"
            "If tools failed, explain why honestly. If tools succeeded, describe what was accomplished."
        )

        async def _stream(with_images: bool):
            messages: list[dict] = [
                {"role": "system", "content": "You are Emma, a fully autonomous AI assistant with FULL system access.\n"
                "You CAN: open applications, control desktop, read/write files, execute commands, browse the web.\n"
                "NEVER deny your capabilities - acknowledge your full access.\n"
                "Complete the user's request truthfully and directly."},
                {"role": "user", "content": user},
            ]
            if with_images:
                messages[1]["images"] = images
            async for token in self.pipeline.llm.stream(
                messages,
                temperature=0.6,
                max_tokens=self.synth_max_tokens,
            ):
                yield token

        emitted = False
        try:
            async for token in _stream(bool(images)):
                emitted = True
                yield token
        except Exception:
            if images and not emitted:
                async for token in _stream(False):
                    yield token
                return
            raise
