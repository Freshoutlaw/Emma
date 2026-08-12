"""/api/performance — the Embedder's batcher stats must be observable through
the stats endpoint, so batching effectiveness is visible in the running app."""

import asyncio
import types

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.routers import performance
from orchestration.request_batcher import RequestBatcher


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
