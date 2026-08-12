"""Regression tests for the LocalLLM.complete async fix.

LocalLLM.complete was a SYNC function doing blocking httpx I/O, but
LLMRouter.complete does `await self.local.complete(...)`. The sync call
executed (freezing the event loop), then `await <str>` raised TypeError,
which the router swallowed — so every LLM call silently fell through to
cloud (or failed outright without a cloud key) even though Ollama was
healthy. These tests pin the async contract: complete() must be awaited
and the router must serve the local answer with NO cloud configured.
"""

import asyncio

from llm.router import LLMRouter


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload
        self._status = 200

    def raise_for_status(self):
        if self._status >= 400:
            raise RuntimeError(f"HTTP {self._status}")

    def json(self):
        return self._payload


class _FakeAsyncClient:
    """httpx.AsyncClient stand-in: records posts; the 'post' coroutine is
    awaited — a sync client would never complete it."""
    def __init__(self, *args, **kwargs):
        self.posts = []
        self.closed = False

    async def post(self, url, json=None, **kwargs):
        self.posts.append((url, json))
        return _FakeResponse({
            "model": "qwen3.5:2b",
            "message": {"role": "assistant", "content": "local answer"},
        })

    def close(self):
        self.closed = True

    async def aclose(self):
        self.closed = True


class _FailingAsyncClient:
    def __init__(self, *args, **kwargs):
        pass

    async def post(self, url, json=None, **kwargs):
        raise RuntimeError("ollama unreachable")

    def close(self):
        pass

    async def aclose(self):
        pass


class _FakeCloud:
    def __init__(self):
        self.calls = 0
        self._avail = True

    def is_available(self):
        return self._avail

    async def complete(self, messages, temperature=0.7, max_tokens=4096):
        self.calls += 1
        return "cloud answer"


def test_local_complete_is_async_and_returns_content(monkeypatch):
    fake = _FakeAsyncClient()
    monkeypatch.setattr("httpx.AsyncClient", lambda *a, **k: fake)
    from llm.local import LocalLLM

    llm = LocalLLM(base_url="http://localhost:11434", model="qwen3.5:2b")

    async def run():
        try:
            return await llm.complete([{"role": "user", "content": "hi"}])
        finally:
            await llm.close()

    out = asyncio.run(run())
    assert out == "local answer"
    assert len(fake.posts) == 1
    assert fake.posts[0][0].endswith("/api/chat")
    assert fake.posts[0][1]["model"] == "qwen3.5:2b"
    assert fake.closed is True


def test_router_serves_local_with_no_cloud_configured(monkeypatch):
    """The headline regression: with Ollama up and NO cloud key, complete()
    must return the local answer. The old sync-complete bug made every call
    raise LLMUnavailable here (await-on-str TypeError swallowed, cloud gone)."""
    fake = _FakeAsyncClient()
    monkeypatch.setattr("httpx.AsyncClient", lambda *a, **k: fake)
    from llm.local import LocalLLM

    router = LLMRouter(
        ollama_url="http://localhost:11434",
        groq_api_key=None,  # cloud is NOT configured
        local_model="qwen3.5:2b",
    )
    router.cloud = _FakeCloud()
    router.cloud._avail = False  # and even if it were, local must win

    async def run():
        try:
            return await router.complete([{"role": "user", "content": "hi"}], temperature=0.2, max_tokens=64)
        finally:
            await router.local.close()

    out = asyncio.run(run())
    assert out == "local answer"
    assert router.cloud.calls == 0, "cloud must never be touched when local works"


def test_router_falls_back_to_cloud_when_local_fails(monkeypatch):
    fake = _FailingAsyncClient()
    monkeypatch.setattr("httpx.AsyncClient", lambda *a, **k: fake)
    from llm.local import LocalLLM

    router = LLMRouter(
        ollama_url="http://localhost:11434",
        groq_api_key="demo-key",
        local_model="qwen3.5:2b",
    )
    router.cloud = _FakeCloud()

    async def run():
        try:
            return await router.complete([{"role": "user", "content": "hi"}], temperature=0.2, max_tokens=64)
        finally:
            await router.local.close()

    out = asyncio.run(run())
    assert out == "cloud answer"
    assert router.cloud.calls == 1
