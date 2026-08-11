"""Tests for LLMRouter.stream fallback semantics.

A local-provider failure must fall back to cloud only when nothing has been
emitted yet; once tokens have been delivered, re-streaming from cloud would
duplicate them, so the failure must propagate instead.
"""

import asyncio

from llm.router import LLMRouter


class _FakeLocal:
    def __init__(self, behavior):
        self.behavior = behavior  # "ok" | "fail_first" | "fail_mid"

    def available_models(self):
        return ["local-model"]

    def is_available(self):
        return True

    async def stream(self, messages, temperature=0.7, max_tokens=4096, model=None):
        if self.behavior == "fail_first":
            raise RuntimeError("local died before first token")
        if self.behavior == "fail_mid":
            yield "Hello"
            yield " "
            raise RuntimeError("local died mid-stream")
        yield "local"
        yield " answer"


class _FakeCloud:
    def __init__(self):
        self.calls = 0

    def is_available(self):
        return True

    async def stream(self, messages, temperature=0.7, max_tokens=4096):
        self.calls += 1
        yield "cloud"
        yield " answer"


def _router(local_behavior, domain="localhost"):
    router = LLMRouter(domain=domain)
    router.local = _FakeLocal(local_behavior)
    router.cloud = _FakeCloud()
    return router


def test_fallback_to_cloud_when_local_fails_before_any_token():
    router = _router("fail_first")

    async def run():
        return [t async for t in router.stream([{"role": "user", "content": "hi"}])]

    assert asyncio.run(run()) == ["cloud", " answer"]
    assert router.cloud.calls == 1


def test_no_cloud_restart_after_local_emitted_tokens():
    """Regression: a mid-stream local failure must NOT re-emit tokens from cloud."""
    router = _router("fail_mid")

    async def run():
        return [t async for t in router.stream([{"role": "user", "content": "hi"}])]

    try:
        asyncio.run(run())
        raised = False
    except RuntimeError as exc:
        raised = True
        assert "mid-stream" in str(exc)
    assert raised is True
    assert router.cloud.calls == 0


def test_local_success_never_touches_cloud():
    router = _router("ok")

    async def run():
        return [t async for t in router.stream([{"role": "user", "content": "hi"}])]

    assert asyncio.run(run()) == ["local", " answer"]
    assert router.cloud.calls == 0


def test_cloud_only_domain_streams_from_cloud():
    router = _router("ok", domain="onrender.com")

    async def run():
        return [t async for t in router.stream([{"role": "user", "content": "hi"}])]

    assert asyncio.run(run()) == ["cloud", " answer"]
    assert router.cloud.calls == 1
