"""Tests for Ollama Cloud-first routing with a local-model fallback.

When `ollama_cloud_model` is configured (e.g. gpt-oss:120b-cloud), the router
must try it FIRST and transparently fall back to the locally-pulled model
when the cloud call fails (free-tier quota / rate limit / subscription).
A mid-stream cloud failure must never re-emit tokens from the fallback.
"""

import asyncio

from llm.router import LLMRouter


class _FakeProvider:
    """Stand-in for LocalLLM/CloudLLM with controllable complete/stream behavior."""

    def __init__(self, name, complete_behavior="ok", stream_behavior="ok"):
        self.name = name
        self.model = f"{name}-model"
        self.calls = 0
        self.complete_behavior = complete_behavior  # "ok" | "fail"
        self.stream_behavior = stream_behavior      # "ok" | "fail_first" | "fail_mid"

    def available_models(self):
        return [self.model]

    def is_available(self):
        return True

    async def complete(self, messages, temperature=0.7, max_tokens=4096, model=None):
        self.calls += 1
        if self.complete_behavior == "fail":
            raise RuntimeError(f"{self.name} complete failed")
        return f"{self.name} answer"

    async def stream(self, messages, temperature=0.7, max_tokens=4096, model=None):
        self.calls += 1
        if self.stream_behavior == "fail_first":
            raise RuntimeError(f"{self.name} failed before first token")
        if self.stream_behavior == "fail_mid":
            yield f"{self.name} "
            raise RuntimeError(f"{self.name} failed mid-stream")
        yield f"{self.name} "
        yield "answer"


def _router(cloud_ok=True, cloud_stream="ok", local_ok=True, local_stream="ok"):
    router = LLMRouter(domain="localhost", ollama_cloud_model="gpt-oss:120b-cloud")
    router.ollama_cloud = _FakeProvider(
        "cloud",
        complete_behavior="ok" if cloud_ok else "fail",
        stream_behavior=cloud_stream,
    )
    router.local = _FakeProvider(
        "local",
        complete_behavior="ok" if local_ok else "fail",
        stream_behavior=local_stream,
    )
    router.cloud = _FakeProvider("groq")
    return router


# ------------------------------------------------------------------ complete
def test_complete_cloud_first_when_cloud_works():
    router = _router()
    out = asyncio.run(router.complete([{"role": "user", "content": "hi"}]))
    assert out == "cloud answer"
    assert router.ollama_cloud.calls == 1
    assert router.local.calls == 0
    assert router.cloud.calls == 0


def test_complete_falls_back_to_local_when_cloud_fails():
    """The headline behavior: cloud quota exhausted -> local model answers."""
    router = _router(cloud_ok=False)
    out = asyncio.run(router.complete([{"role": "user", "content": "hi"}]))
    assert out == "local answer"
    assert router.ollama_cloud.calls == 1
    assert router.local.calls == 1
    assert router.cloud.calls == 0


def test_complete_falls_through_to_groq_when_both_fail():
    router = _router(cloud_ok=False, local_ok=False)
    out = asyncio.run(router.complete([{"role": "user", "content": "hi"}]))
    assert out == "groq answer"
    assert router.ollama_cloud.calls == 1
    assert router.local.calls == 1
    assert router.cloud.calls == 1


# ------------------------------------------------------------------- stream
def test_stream_cloud_first_when_cloud_works():
    router = _router()
    tokens = asyncio.run(_collect(router.stream([{"role": "user", "content": "hi"}])))
    assert tokens == ["cloud ", "answer"]
    assert router.ollama_cloud.calls == 1
    assert router.local.calls == 0


def test_stream_falls_back_to_local_when_cloud_fails_before_first_token():
    """Cloud rate-limit errors surface before any token — safe to fall back."""
    router = _router(cloud_stream="fail_first")
    tokens = asyncio.run(_collect(router.stream([{"role": "user", "content": "hi"}])))
    assert tokens == ["local ", "answer"]
    assert router.ollama_cloud.calls == 1
    assert router.local.calls == 1


def test_stream_never_restarts_local_after_cloud_emitted_tokens():
    """Mid-stream cloud failure must propagate, not duplicate tokens locally."""
    router = _router(cloud_stream="fail_mid")
    try:
        asyncio.run(_collect(router.stream([{"role": "user", "content": "hi"}])))
        raised = False
    except RuntimeError as exc:
        raised = True
        assert "mid-stream" in str(exc)
    assert raised is True
    assert router.local.calls == 0


# -------------------------------------------------------------------- model
def test_model_prefers_ollama_cloud_when_configured():
    router = _router()
    assert router.model() == router.ollama_cloud.model


async def _collect(agen):
    return [t async for t in agen]


# --------------------------------------------------------------- circuit breaker
import time

import pytest

from llm.router import OLLAMA_CLOUD_CIRCUIT_KEY
from orchestration.failure_isolation import failure_isolation


@pytest.fixture(autouse=True)
def _reset_cloud_circuit():
    """The failure_isolation singleton persists across tests — reset the cloud
    circuit before and after each test so failures don't leak between them."""
    failure_isolation.reset(OLLAMA_CLOUD_CIRCUIT_KEY)
    yield
    failure_isolation.reset(OLLAMA_CLOUD_CIRCUIT_KEY)


def test_cloud_circuit_opens_and_skips_doomed_attempts():
    """After 3 consecutive cloud failures the circuit opens: turn 4 serves
    from local WITHOUT attempting the cloud at all."""
    router = _router(cloud_ok=False)
    for _ in range(3):
        out = asyncio.run(router.complete([{"role": "user", "content": "hi"}]))
        assert out == "local answer"
    assert router.ollama_cloud.calls == 3
    # Circuit is now OPEN — the 4th turn must not touch the cloud.
    out = asyncio.run(router.complete([{"role": "user", "content": "hi"}]))
    assert out == "local answer"
    assert router.ollama_cloud.calls == 3, "cloud must not be attempted while OPEN"
    assert router.local.calls == 4
    circuit = failure_isolation._circuit(OLLAMA_CLOUD_CIRCUIT_KEY)
    assert circuit.total_rejected == 1


def test_cloud_retried_after_cooldown_elapses():
    """Once the cooldown has passed, the circuit goes half-open and probes the
    cloud again; a successful probe closes it and cloud service resumes."""
    router = _router(cloud_ok=True)  # cloud healthy again after the outage
    # Trip the circuit with a failing provider.
    router_fail = _router(cloud_ok=False)
    for _ in range(3):
        asyncio.run(router_fail.complete([{"role": "user", "content": "hi"}]))
    circuit = failure_isolation._circuit(OLLAMA_CLOUD_CIRCUIT_KEY)
    assert circuit.state.value == "open"
    # Simulate the cooldown elapsing.
    circuit.last_failure_time = time.monotonic() - 999
    out = asyncio.run(router.complete([{"role": "user", "content": "hi"}]))
    assert out == "cloud answer"
    assert router.ollama_cloud.calls == 1
    assert circuit.state.value == "closed"
    # Circuit closed — subsequent turns use the cloud normally.
    out = asyncio.run(router.complete([{"role": "user", "content": "hi"}]))
    assert out == "cloud answer"
    assert router.ollama_cloud.calls == 2


def test_cloud_probe_failure_reopens_circuit():
    """A failed probe after cooldown re-opens the circuit immediately, so the
    next turn skips the cloud again instead of paying per-turn failures."""
    router = _router(cloud_ok=False)
    for _ in range(3):
        asyncio.run(router.complete([{"role": "user", "content": "hi"}]))
    circuit = failure_isolation._circuit(OLLAMA_CLOUD_CIRCUIT_KEY)
    circuit.last_failure_time = time.monotonic() - 999  # cooldown elapsed
    # Probe fires, fails again -> re-opens.
    asyncio.run(router.complete([{"role": "user", "content": "hi"}]))
    assert router.ollama_cloud.calls == 4
    assert circuit.state.value == "open"
    # Next turn: circuit OPEN -> cloud skipped, local answers.
    out = asyncio.run(router.complete([{"role": "user", "content": "hi"}]))
    assert out == "local answer"
    assert router.ollama_cloud.calls == 4, "cloud must be skipped after re-open"


# ------------------------------------------------------------- served tracking
def test_last_served_reports_cloud_when_cloud_serves():
    router = _router()
    asyncio.run(router.complete([{"role": "user", "content": "hi"}]))
    assert router.last_served == {"provider": "cloud", "model": "cloud-model"}


def test_last_served_reports_local_fallback_when_cloud_fails():
    router = _router(cloud_ok=False)
    asyncio.run(router.complete([{"role": "user", "content": "hi"}]))
    assert router.last_served == {"provider": "local", "model": "local-model"}


def test_last_served_reports_local_when_no_ollama_cloud_configured():
    router = LLMRouter(domain="localhost")  # ollama_cloud defaults to None
    fake = _FakeProvider("local")
    router.local = fake
    router.cloud = _FakeProvider("groq")
    asyncio.run(router.complete([{"role": "user", "content": "hi"}]))
    assert router.last_served == {"provider": "local", "model": "local-model"}
