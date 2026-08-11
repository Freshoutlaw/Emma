"""Tests for the orchestration layer — Tier 2.

Covers least-privilege tool scoping (per-agent allowlists enforced at the
execute boundary and advertised in the plan prompt) and explicit per-agent
bounds (max plan steps, max tokens), including the end-to-end "a plan step
outside the allowlist is skipped as data, never executed" path.
"""

import asyncio
import types

from agents.base import BaseAgent
from agents.control import ControlAgent, ToolNotAllowedError, UnknownToolError
from agents.reasoning import ReasoningAgent


# ---------------------------------------------------------------- fixtures

class _Cap:
    """A capability stub exposing every attribute `_tools()` references."""

    def __init__(self) -> None:
        for name in (
            "read_file", "write_file", "list_dir", "run_command",
            "search", "fetch_page_text", "status", "log", "commit", "push",
            "ps", "images", "logs", "compose_up", "compose_down", "publish",
            "open", "screenshot", "notify",
        ):
            setattr(self, name, self._call)

    async def _call(self, *args, **kwargs) -> str:
        return "ok"


def _control(agents: dict | None = None) -> ControlAgent:
    """A ControlAgent with a stub pipeline — no real capabilities touched.

    __new__ skips __init__, so the capability attributes `_tools()` reads are
    set here directly.
    """
    pipeline = types.SimpleNamespace(
        audit=types.SimpleNamespace(log=lambda *a, **k: None),
        reasoning=types.SimpleNamespace(tool_allowlist=ReasoningAgent.tool_allowlist),
        io=_Cap(), web=_Cap(), git=_Cap(), docker=_Cap(),
        mqtt=_Cap(), browser=_Cap(), desktop=_Cap(),
    )
    for name, agent in (agents or {}).items():
        setattr(pipeline, name, agent)
    agent = ControlAgent.__new__(ControlAgent)
    agent.pipeline = pipeline
    agent.io = pipeline.io
    agent.web = pipeline.web
    agent.git = pipeline.git
    agent.docker = pipeline.docker
    agent.mqtt = pipeline.mqtt
    agent.browser = pipeline.browser
    agent.desktop = pipeline.desktop
    return agent


def _pipeline(tmp_path):
    """A real Pipeline (safe: nothing connects at init) re-homed to tmp_path."""
    from backend.config import Settings
    from agents.router import Pipeline

    pipeline = Pipeline(Settings())
    pipeline.settings.project_root = tmp_path
    return pipeline


# ------------------------------------------------- scoping: allowlist contents

def test_reasoning_allowlist_excludes_irreversible_tools():
    assert "read_file" in ReasoningAgent.tool_allowlist
    assert "web_search" in ReasoningAgent.tool_allowlist
    assert "git_push" not in ReasoningAgent.tool_allowlist
    assert "compose_down" not in ReasoningAgent.tool_allowlist
    assert "mqtt_publish" not in ReasoningAgent.tool_allowlist


def test_agent_without_allowlist_gets_full_catalog():
    """tool_allowlist=None (the BaseAgent default) means full catalog."""
    pipeline = types.SimpleNamespace(tool_allowlist=None)
    agent = _control(agents={"loose": pipeline})
    assert agent._allowlist_for("loose") == frozenset(agent._tools())


def test_control_actor_always_has_full_catalog():
    agent = _control()
    assert "git_push" in agent._allowlist_for("control")


# ------------------------------------------------- scoping: execute boundary

def test_execute_denies_tool_outside_actor_allowlist():
    agent = _control()
    try:
        asyncio.run(agent.execute("git_push", actor="reasoning"))
        raised = False
    except ToolNotAllowedError as exc:
        raised = True
        assert exc.tool == "git_push"
        assert exc.actor == "reasoning"
        assert "not available to agent 'reasoning'" in str(exc)
    assert raised is True


def test_execute_allows_tool_inside_actor_allowlist():
    agent = _control()
    assert asyncio.run(agent.execute("web_search", actor="reasoning")) == "ok"


def test_execute_unknown_tool_still_raises():
    agent = _control()
    try:
        asyncio.run(agent.execute("no_such_tool", actor="reasoning"))
        raised = False
    except UnknownToolError:
        raised = True
    assert raised is True


# ------------------------------------------------- scoping: plan prompt

def test_catalog_filtered_to_allowlist():
    agent = ReasoningAgent.__new__(ReasoningAgent)
    catalog = agent._catalog()
    assert set(catalog) == set(ReasoningAgent.tool_allowlist)
    assert "git_push" not in catalog
    assert "list_dir" in catalog


def test_system_prompt_advertises_only_allowed_tools():
    agent = ReasoningAgent.__new__(ReasoningAgent)
    prompt = agent._system_prompt()
    assert '"list_dir"' in prompt
    assert '"web_search"' in prompt
    assert '"git_push"' not in prompt
    assert f"at most {agent.max_plan_steps} steps" in prompt


# ------------------------------------------------- bounds

def test_plan_capped_at_max_steps(tmp_path):
    pipeline = _pipeline(tmp_path)
    try:
        agent = pipeline.reasoning
        steps = [{"tool": "list_dir", "args": {}}] * (agent.max_plan_steps + 5)
        capped = agent._cap_plan(steps)
        assert len(capped) == agent.max_plan_steps
    finally:
        asyncio.run(pipeline.close())


def test_plan_under_cap_untouched(tmp_path):
    pipeline = _pipeline(tmp_path)
    try:
        agent = pipeline.reasoning
        steps = [{"tool": "list_dir", "args": {}}] * 3
        assert agent._cap_plan(steps) == steps
    finally:
        asyncio.run(pipeline.close())


# ------------------------------------------------- end-to-end: run + stream

def test_reasoning_run_skips_disallowed_step(tmp_path):
    """A plan step outside the allowlist is skipped as data, never executed."""
    pipeline = _pipeline(tmp_path)
    try:
        async def run():
            async def fake_plan(request, context=""):
                return [
                    {"tool": "list_dir", "args": {"path": "."}},
                    {"tool": "git_push", "args": {}},
                ]
            async def fake_augment(request, k=4):
                return ""
            async def fake_synth(request, context, outputs):
                return "\n".join(outputs)

            pipeline.reasoning.plan = fake_plan
            pipeline.rag.augment = fake_augment
            pipeline.reasoning._synthesize = fake_synth
            return await pipeline.reasoning.run("list files and push them")

        result = asyncio.run(run())
        assert result.ok is True
        assert "not in this agent's tool allowlist" in result.output
        executed = [a["tool"] for a in result.actions]
        assert executed == ["list_dir"]
        assert "git_push" not in executed
    finally:
        asyncio.run(pipeline.close())


def test_stream_emits_no_action_for_disallowed_step(tmp_path):
    """The SSE stream path skips a disallowed tool silently (no action event)."""
    pipeline = _pipeline(tmp_path)

    async def run():
        async def fake_classify(message):
            return {"intent": "reasoning", "tool": None, "args": {}}
        async def fake_plan(message, context):
            return [{"tool": "git_push", "args": {}}]
        async def fake_augment(request, k=4):
            return ""
        async def fake_stream_narration(request, context, outputs):
            yield "done"

        pipeline.router.classify = fake_classify
        pipeline.reasoning.plan = fake_plan
        pipeline.rag.augment = fake_augment
        pipeline.reasoning.stream_narration = fake_stream_narration
        try:
            return [e async for e in pipeline.router.stream("push the branch")]
        finally:
            # Close inside the same event loop that created the pipeline's
            # pooled httpx clients (created lazily on first use).
            await pipeline.close()

    events = asyncio.run(run())
    assert not any(e["type"] == "action" for e in events)
    actions = [e for e in events if e["type"] == "actions"]
    assert actions == [{"type": "actions", "actions": []}]


def test_dispatch_control_tool_bypasses_scoping(tmp_path):
    """A direct user tool request routes with full access (actor 'control')."""
    pipeline = _pipeline(tmp_path)
    try:
        async def run():
            async def fake_classify(message):
                return {"intent": "control", "tool": "list_dir", "args": {"path": str(tmp_path)}}
            pipeline.router.classify = fake_classify
            result = await pipeline.router.dispatch("list the temp directory")
            return result

        result = asyncio.run(run())
        assert result.ok is True
        assert result.actions == [{"tool": "list_dir", "args": {"path": str(tmp_path)}}]
    finally:
        asyncio.run(pipeline.close())
