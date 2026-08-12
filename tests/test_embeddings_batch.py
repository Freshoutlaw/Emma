"""Integration tests for the batched Embedder path.

Concurrent embed() calls must coalesce through the RequestBatcher into one
Ollama `/api/embed` call, and a missing embedding model must short-circuit
to the deterministic fallback without network calls or batch latency.
"""

import asyncio

from memory.embeddings import Embedder


def _fake_client(models=("nomic-embed-text",), embed_status=200, legacy_status=200):
    """httpx.AsyncClient stand-in with /api/embed, /api/embeddings, /api/tags."""

    class FakeResponse:
        def __init__(self, payload, status=200):
            self._payload = payload
            self._status = status

        def raise_for_status(self):
            if self._status >= 400:
                raise RuntimeError(f"HTTP {self._status}")

        def json(self):
            return self._payload

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            self.posts = []
            self.gets = []

        async def post(self, url, json=None, **kwargs):
            self.posts.append((url, json))
            if url.endswith("/api/embed"):
                if embed_status >= 400:
                    return FakeResponse({}, embed_status)
                inputs = json.get("input", [])
                return FakeResponse({"embeddings": [[float(len(t)), 1.0] for t in inputs]})
            if url.endswith("/api/embeddings"):
                if legacy_status >= 400:
                    return FakeResponse({}, legacy_status)
                return FakeResponse({"embedding": [float(len(json["prompt"])), 1.0]})
            return FakeResponse({}, 404)

        async def get(self, url, **kwargs):
            self.gets.append(url)
            return FakeResponse({"models": [{"name": m} for m in models]})

        async def aclose(self):
            return None

    return FakeAsyncClient()


def test_embed_coalesces_concurrent_calls_into_one_batch(monkeypatch):
    fake = _fake_client()
    monkeypatch.setattr("httpx.AsyncClient", lambda *a, **k: fake)
    embedder = Embedder("http://localhost:11434", "nomic-embed-text", dim=2)

    async def run():
        try:
            return await asyncio.gather(embedder.embed("abc"), embedder.embed("defgh"))
        finally:
            await embedder.close()

    results = asyncio.run(run())
    embed_posts = [p for p in fake.posts if p[0].endswith("/api/embed")]
    assert len(embed_posts) == 1, f"expected one batch call, got {len(embed_posts)}"
    inputs = embed_posts[0][1]["input"]
    assert set(inputs) == {"abc", "defgh"}
    assert results == [[3.0, 1.0], [5.0, 1.0]]


def test_embed_batch_uses_one_batch_call(monkeypatch):
    fake = _fake_client()
    monkeypatch.setattr("httpx.AsyncClient", lambda *a, **k: fake)
    embedder = Embedder("http://localhost:11434", "nomic-embed-text", dim=2)

    async def run():
        try:
            return await embedder.embed_batch(["a", "bb", "ccc"])
        finally:
            await embedder.close()

    results = asyncio.run(run())
    embed_posts = [p for p in fake.posts if p[0].endswith("/api/embed")]
    assert len(embed_posts) == 1
    assert len(embed_posts[0][1]["input"]) == 3
    assert results == [[1.0, 1.0], [2.0, 1.0], [3.0, 1.0]]


def test_embed_short_circuits_when_model_missing(monkeypatch):
    fake = _fake_client(models=("qwen3.5:2b",))  # no embedding model
    monkeypatch.setattr("httpx.AsyncClient", lambda *a, **k: fake)
    embedder = Embedder("http://localhost:11434", "nomic-embed-text", dim=2)

    async def run():
        try:
            return await embedder.embed("hello world")
        finally:
            await embedder.close()

    vector = asyncio.run(run())
    assert vector == embedder._fallback("hello world")
    assert fake.posts == [], "no network embed should be attempted when the model is missing"


def test_embed_falls_back_per_text_when_batch_endpoint_fails(monkeypatch):
    fake = _fake_client(embed_status=500)  # /api/embed broken, legacy works
    monkeypatch.setattr("httpx.AsyncClient", lambda *a, **k: fake)
    embedder = Embedder("http://localhost:11434", "nomic-embed-text", dim=2)

    async def run():
        try:
            return await asyncio.gather(embedder.embed("ab"), embedder.embed("cdef"))
        finally:
            await embedder.close()

    results = asyncio.run(run())
    legacy_posts = [p for p in fake.posts if p[0].endswith("/api/embeddings")]
    assert len(legacy_posts) == 2
    assert results == [[2.0, 1.0], [4.0, 1.0]]


def test_embed_total_failure_yields_deterministic_fallback(monkeypatch):
    fake = _fake_client(embed_status=500, legacy_status=500)
    monkeypatch.setattr("httpx.AsyncClient", lambda *a, **k: fake)
    embedder = Embedder("http://localhost:11434", "nomic-embed-text", dim=2)

    async def run():
        try:
            return await embedder.embed("some content here")
        finally:
            await embedder.close()

    vector = asyncio.run(run())
    assert vector == embedder._fallback("some content here")


def test_fallback_matches_observed_model_dim(monkeypatch):
    """Real embeddings (e.g. nomic-embed-text -> 768-dim) must not mix with a
    configured-dim fallback: after the first real vector, the deterministic
    fallback adopts the observed dimension so cosine scoring never truncates."""
    # The model returns 3-dim vectors while the config claims dim=2 — the
    # exact live condition (nomic-embed-text -> 768 vs configured 384).
    class Fake3:
        def __init__(self, *args, **kwargs):
            self.posts = []

        async def post(self, url, json=None, **kwargs):
            self.posts.append((url, json))
            if url.endswith("/api/embed"):
                inputs = json.get("input", [])
                return type("R", (), {"raise_for_status": lambda s: None,
                                      "json": lambda s: {"embeddings": [[float(len(t)), 0.0, 1.0] for t in inputs]}})()
            return type("R", (), {"raise_for_status": lambda s: None, "json": lambda s: {}})()

        async def get(self, url, **kwargs):
            return type("R", (), {"raise_for_status": lambda s: None,
                                  "json": lambda s: {"models": [{"name": "nomic-embed-text"}]}})()

        async def aclose(self):
            return None

    monkeypatch.setattr("httpx.AsyncClient", lambda *a, **k: Fake3())
    embedder = Embedder("http://localhost:11434", "nomic-embed-text", dim=2)

    async def run():
        try:
            vector = await embedder.embed("hello world")
            return vector, embedder._fallback("goodbye moon")
        finally:
            await embedder.close()

    vector, fallback = asyncio.run(run())
    assert len(vector) == 3
    assert embedder._observed_dim == 3
    assert len(fallback) == 3, "fallback must adopt the observed model dim, not the stale config"


def test_fallback_uses_configured_dim_before_any_real_embed(monkeypatch):
    """Before any real embedding is seen, the fallback uses the configured dim
    (matches a model that is present but never successfully queried)."""
    fake = _fake_client(models=("qwen3.5:2b",))  # model missing -> no network
    monkeypatch.setattr("httpx.AsyncClient", lambda *a, **k: fake)
    embedder = Embedder("http://localhost:11434", "nomic-embed-text", dim=4)

    async def run():
        try:
            vector = await embedder.embed("some content here")
            return vector
        finally:
            await embedder.close()

    vector = asyncio.run(run())
    assert vector == embedder._fallback("some content here")
    assert len(vector) == 4
    assert embedder._observed_dim is None
