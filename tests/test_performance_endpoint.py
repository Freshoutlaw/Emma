"""/api/performance — the Embedder's batcher stats must be observable through
the stats endpoint, so batching effectiveness is visible in the running app."""

import asyncio
import types

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agents.base import AgentResult
from agents.router import AgentRouter
from backend.routers import performance
from orchestration.request_batcher import RequestBatcher
from performance.turn_metrics import turn_metrics


class _StubEmbedder:
    def __init__(self):
        self._batcher = RequestBatcher(
            lambda payloads: list(payloads), max_batch_size=8, max_wait_time=0.02
        )
        self.cache_stats_calls = 0

    def get_cache_stats(self):
        self.cache_stats_calls += 1
        return {"size": 0, "type": "none"}


def _app(embedder=None):
    app = FastAPI()
    app.include_router(performance.router)
    pipeline = types.SimpleNamespace(embedder=embedder) if embedder is not None else types.SimpleNamespace()
    app.state.pipeline = pipeline
    return app


def test_stats_reports_embedder_batcher():
    client = TestClient(_app(_StubEmbedder()))
    data = client.get("/api/performance/stats").json()

    batcher = data["embedder"]["batcher"]
    assert data["embedder"]["status"] == "ok"
    assert batcher["batches_completed"] == 0
    assert batcher["requests_processed"] == 0
    assert batcher["max_batch_size"] == 8
    assert batcher["max_wait_time"] == 0.02
    assert "queue_size" in batcher and "pending_futures" in batcher


def test_stats_reflects_live_batching():
    embedder = _StubEmbedder()
    app = _app(embedder)

    async def run():
        return await asyncio.gather(*(embedder._batcher.submit(i) for i in range(4)))

    assert asyncio.run(run()) == [0, 1, 2, 3]

    data = TestClient(app).get("/api/performance/stats").json()
    batcher = data["embedder"]["batcher"]
    assert batcher["batches_completed"] == 1, "4 concurrent submits -> 1 batch"
    assert batcher["requests_processed"] == 4
    assert data["embedder"]["cache"] == {"size": 0, "type": "none"}
    assert embedder.cache_stats_calls >= 1


def test_stats_graceful_without_embedder():
    client = TestClient(_app())
    data = client.get("/api/performance/stats").json()
    assert data["embedder"]["status"] == "no_embedder"


def test_cache_endpoint_still_works():
    embedder = _StubEmbedder()
    client = TestClient(_app(embedder))
    data = client.get("/api/performance/cache/embeddings").json()
    assert data == {"size": 0, "type": "none"}


# ---------------------------------------------------------------------------
# Per-turn latency + local-vs-cloud routing counters (turn_metrics singleton)


@pytest.fixture(autouse=True)
def _reset_turn_metrics():
    """The collector is a process singleton — isolate each test."""
    turn_metrics.reset()
    yield
    turn_metrics.reset()


def test_stats_reports_turn_metrics():
    turn_metrics.record_turn("reasoning", 1.5)
    turn_metrics.record_turn("memory", 0.3)
    turn_metrics.record_turn("reasoning", 2.5)
    turn_metrics.record_classify(fast_path=True)
    turn_metrics.record_classify(fast_path=False)
    turn_metrics.record_llm_call("local", ok=True)
    turn_metrics.record_llm_call("local", ok=False)
    turn_metrics.record_llm_call("cloud", ok=True)

    data = TestClient(_app(_StubEmbedder())).get("/api/performance/stats").json()
    turns = data["turns"]
    assert turns["count"] == 3
    assert turns["latency_seconds"]["samples"] == 3
    assert turns["latency_seconds"]["avg"] == pytest.approx(4.3 / 3)
    assert turns["latency_seconds"]["p50"] == pytest.approx(1.5)
    assert turns["latency_seconds"]["p95"] == pytest.approx(2.4)  # linear interp: 1.5 + 0.9*(2.5-1.5)
    assert turns["turns_by_intent"] == {"reasoning": 2, "memory": 1}
    assert turns["classify"] == {"llm_calls": 1, "fast_path": 1}
    assert turns["llm_routing"] == {"local_calls": 1, "local_failures": 1, "cloud_calls": 1}


def test_classify_records_fast_path_vs_llm():
    class _RecordingLLM:
        def route(self):
            return "local"

        async def complete(self, messages, temperature=0.0, max_tokens=200):
            return '{"intent": "chat", "confidence": 0.3}'

    router = AgentRouter(types.SimpleNamespace(llm=_RecordingLLM()))

    result = asyncio.run(router.classify("hello world"))
    assert result["intent"] == "reasoning"
    snap = turn_metrics.snapshot()
    assert snap["classify"] == {"llm_calls": 0, "fast_path": 1}

    asyncio.run(router.classify("tell me something interesting about the universe"))
    snap = turn_metrics.snapshot()
    assert snap["classify"] == {"llm_calls": 1, "fast_path": 1}


def test_router_run_records_turn_with_intent():
    class _FakeAudit:
        def log(self, event, **kwargs):
            return {"event": event}

    class _FakeLLM:
        def route(self):
            return "none"  # skip LLM intent classification

    class _FakeReasoning:
        async def run(self, message):
            return AgentResult(ok=True, output="ok", intent="reasoning")

    class _FakeEpisodic:
        async def remember(self, content, kind="episode", payload=None):
            return "ep1"

    class _StubAgent:
        async def run(self, message):
            raise AssertionError("unreachable: intent should be reasoning")

    pipeline = types.SimpleNamespace(
        audit=_FakeAudit(),
        llm=_FakeLLM(),
        reasoning=_FakeReasoning(),
        episodic=_FakeEpisodic(),
        control=_StubAgent(),
        memory_agent=_StubAgent(),
        security_agent=_StubAgent(),
        self_improve=_StubAgent(),
        map_agent=_StubAgent(),
        supabase_query_agent=_StubAgent(),
        design_agent=_StubAgent(),
        research_agent=_StubAgent(),
        agent_factory=_StubAgent(),
        learning_agent=_StubAgent(),
    )
    router = AgentRouter(pipeline)

    result = asyncio.run(router.run("hello"))
    assert result.ok

    snap = turn_metrics.snapshot()
    assert snap["count"] == 1
    assert snap["turns_by_intent"] == {"reasoning": 1}
    assert snap["latency_seconds"]["samples"] == 1
    assert snap["latency_seconds"]["avg"] >= 0


def test_llm_router_counts_local_success():
    from llm.router import LLMRouter

    class _FakeLocal:
        async def complete(self, messages, temperature=0.7, max_tokens=4096, model=None):
            return "local answer"

        def available_models(self):
            return ["qwen3:4b"]

    class _FakeCloud:
        def __init__(self):
            self.calls = 0

        def is_available(self):
            return True

        async def complete(self, messages, temperature=0.7, max_tokens=4096):
            self.calls += 1
            return "cloud answer"

    router = LLMRouter(domain="localhost", local_model="qwen3:4b")
    router.local = _FakeLocal()
    router.cloud = _FakeCloud()

    out = asyncio.run(router.complete([{"role": "user", "content": "hi"}]))
    assert out == "local answer"
    assert router.cloud.calls == 0
    assert turn_metrics.snapshot()["llm_routing"] == {
        "local_calls": 1, "local_failures": 0, "cloud_calls": 0,
    }


def test_llm_router_counts_local_failure_falling_back_to_cloud():
    from llm.router import LLMRouter

    class _FailingLocal:
        async def complete(self, messages, temperature=0.7, max_tokens=4096, model=None):
            raise RuntimeError("ollama down")

        def available_models(self):
            return ["qwen3:4b"]

    class _FakeCloud:
        def __init__(self):
            self.calls = 0

        def is_available(self):
            return True

        async def complete(self, messages, temperature=0.7, max_tokens=4096):
            self.calls += 1
            return "cloud answer"

    router = LLMRouter(domain="localhost", local_model="qwen3:4b")
    router.local = _FailingLocal()
    router.cloud = _FakeCloud()

    out = asyncio.run(router.complete([{"role": "user", "content": "hi"}]))
    assert out == "cloud answer"
    assert router.cloud.calls == 1
    assert turn_metrics.snapshot()["llm_routing"] == {
        "local_calls": 0, "local_failures": 1, "cloud_calls": 1,
    }


def test_clear_resets_turn_metrics():
    turn_metrics.record_turn("reasoning", 1.0)
    client = TestClient(_app())
    assert client.post("/api/performance/clear").json() == {"status": "cleared"}
    snap = turn_metrics.snapshot()
    assert snap["count"] == 0
    assert snap["turns_by_intent"] == {}
    assert snap["llm_routing"] == {"local_calls": 0, "local_failures": 0, "cloud_calls": 0}
