"""AgentRouter.stream audit logging — mid-stream LLM failures must surface as
chat.completed with decision.ok=false (and ok=false in the done event), so a
streamed error is observable in the audit log instead of looking like a
success."""

import asyncio
import types

from agents.router import AgentRouter
from llm.local import LLMUnavailable


class _FakeAudit:
    def __init__(self):
        self.entries = []

    def log(self, event, **kwargs):
        self.entries.append({"event": event, **kwargs})
        return self.entries[-1]


class _FakeLLM:
    def route(self):
        return "none"  # skip LLM intent classification


class _FakeRAG:
    async def augment(self, query, k):
        return ""


class _FakeReasoning:
    def __init__(self, tokens=None, exc=None):
        self.tokens = tokens or []
        self.exc = exc

    async def plan(self, request, context=""):
        return []

    async def stream_narration(self, request, context, outputs, images=None):
        if self.exc is not None:
            raise self.exc
        for token in self.tokens:
            yield token


class _FakeControl:
    async def execute(self, tool, actor=None, **args):
        raise AssertionError("no tools should be planned in these tests")


class _FakeEpisodic:
    async def remember(self, content, kind="episode", payload=None):
        return "ep1"


def _router(tokens=None, exc=None):
    audit = _FakeAudit()
    pipeline = types.SimpleNamespace(
        audit=audit,
        llm=_FakeLLM(),
        rag=_FakeRAG(),
        reasoning=_FakeReasoning(tokens=tokens, exc=exc),
        control=_FakeControl(),
        episodic=_FakeEpisodic(),
    )
    return AgentRouter(pipeline), audit


def _collect(router, message="hello world"):
    async def run():
        events = []
        async for event in router.stream(message):
            events.append(event)
        return events

    return asyncio.run(run())


def _completed(audit):
    return [e for e in audit.entries if e["event"] == "chat.completed"]


def test_stream_success_is_audited_ok_true():
    router, audit = _router(tokens=["Hel", "lo world"])
    events = _collect(router)

    completed = _completed(audit)
    assert len(completed) == 1
    assert completed[0]["action"] == "reasoning"
    assert completed[0]["actor"] == "router"
    assert completed[0]["decision"] == {"ok": True}
    assert completed[0]["detail"]["error"] is None

    done = [e for e in events if e["type"] == "done"]
    assert done and done[0]["result"]["ok"] is True
    tokens = "".join(e["text"] for e in events if e["type"] == "token")
    assert tokens == "Hello world"


def test_stream_llm_unavailable_is_audited_ok_false():
    router, audit = _router(exc=LLMUnavailable("no provider"))
    events = _collect(router)

    completed = _completed(audit)
    assert len(completed) == 1
    assert completed[0]["decision"] == {"ok": False}
    assert completed[0]["detail"]["error"] == "llm_unavailable"

    done = [e for e in events if e["type"] == "done"]
    assert done and done[0]["result"]["ok"] is False
    assert done[0]["result"]["error"] == "llm_unavailable"
    fallback = [e for e in events if e["type"] == "token"]
    assert fallback and fallback[0]["text"].startswith("⚠ No LLM provider")


def test_stream_mid_stream_error_is_audited_ok_false():
    router, audit = _router(exc=RuntimeError("ollama died mid-stream"))
    events = _collect(router)

    completed = _completed(audit)
    assert len(completed) == 1
    assert completed[0]["decision"] == {"ok": False}
    assert completed[0]["detail"]["error"] == "ollama died mid-stream"

    done = [e for e in events if e["type"] == "done"]
    assert done and done[0]["result"]["ok"] is False
    assert done[0]["result"]["error"] == "ollama died mid-stream"
    error_token = [e for e in events if e["type"] == "token"]
    assert error_token and error_token[0]["text"] == "⚠ error: ollama died mid-stream"
