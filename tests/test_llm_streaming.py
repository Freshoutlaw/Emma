"""Regression tests for the LLM streaming fixes.

1. LocalLLM.stream: StopIteration raised by next(gen) inside run_in_executor
   never resolved the awaited future (asyncio quirk), so every locally
   streamed reply HUNG at the end.  The fix catches StopIteration inside the
   worker and signals completion with a sentinel.
2. CloudLLM.stream: the installed groq client (1.x) rejects `stream_options`,
   which raised TypeError on every cloud stream.  The fix dropped the kwarg.
"""

import asyncio
import sys
import types

from llm.local import LocalLLM
from llm.cloud import CloudLLM


async def _collect(agen, timeout: float = 10.0) -> list:
    """Drain an async generator under a hard timeout.

    The local-stream bug was a HANG (the final await never resolved), so a
    regression test that merely iterates would time out the suite instead of
    failing cleanly.  wait_for converts the old hang into a TimeoutError.
    """

    async def drain():
        out = []
        async for chunk in agen:
            out.append(chunk)
        return out

    return await asyncio.wait_for(drain(), timeout)


# ------------------------------------------------------------------ local
def test_local_stream_completes_and_yields_all_tokens(monkeypatch):
    """The StopIteration-in-executor hang regression: a stream that ends with
    a `done: true` chunk must terminate and yield every token."""
    lines = [
        '{"message": {"content": "Hel"}, "done": false}',
        '{"message": {"content": "lo "}, "done": false}',
        '{"message": {"content": "world"}, "done": false}',
        '{"message": {"content": ""}, "done": true, "model": "qwen3:5.4b"}',
    ]

    class FakeResponse:
        def raise_for_status(self):
            return None

        def iter_lines(self):
            return iter(lines)

    class FakeCM:
        def __init__(self, response):
            self._response = response

        def __enter__(self):
            return self._response

        def __exit__(self, *exc):
            return False

    class FakeClient:
        def __init__(self, *args, **kwargs):
            self.calls = []

        def stream(self, method, url, **kwargs):
            self.calls.append((method, url, kwargs))
            return FakeCM(FakeResponse())

    monkeypatch.setattr("llm.local.httpx.Client", FakeClient)
    llm = LocalLLM()

    chunks = asyncio.run(_collect(llm.stream([{"role": "user", "content": "hi"}])))

    assert chunks == ["Hel", "lo ", "world"], "every token must arrive and the stream must terminate"
    method, url, kwargs = llm._client.calls[0]
    assert method == "POST" and url.endswith("/api/chat")
    assert kwargs["json"]["stream"] is True
    assert kwargs["json"]["model"] == "qwen3:5.4b"


def test_local_stream_empty_reply_terminates(monkeypatch):
    """A stream whose first chunk is already `done` (empty reply) must not hang."""
    lines = [
        '{"message": {"content": ""}, "done": true, "model": "qwen3:5.4b"}',
    ]

    class FakeResponse:
        def raise_for_status(self):
            return None

        def iter_lines(self):
            return iter(lines)

    class FakeCM:
        def __enter__(self):
            return FakeResponse()

        def __exit__(self, *exc):
            return False

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def stream(self, method, url, **kwargs):
            return FakeCM()

    monkeypatch.setattr("llm.local.httpx.Client", FakeClient)
    llm = LocalLLM()

    chunks = asyncio.run(_collect(llm.stream([{"role": "user", "content": "hi"}])))
    assert chunks == []


# ------------------------------------------------------------------ cloud
def test_cloud_stream_sends_no_stream_options(monkeypatch):
    """The groq-1.x TypeError regression: create() must receive exactly the
    supported kwargs and never `stream_options`."""
    captured = {}

    class Delta:
        def __init__(self, content):
            self.content = content

    class Choice:
        def __init__(self, delta):
            self.delta = delta

    class Chunk:
        def __init__(self, choices, usage=None, model=None):
            self.choices = choices
            self.usage = usage
            self.model = model

    class FakeCompletions:
        async def create(self, **kwargs):
            captured["kwargs"] = kwargs

            async def gen():
                yield Chunk([Choice(Delta("Hi"))])
                yield Chunk([], usage={"prompt_tokens": 10, "completion_tokens": 5}, model="llama-3.3-70b-versatile")

            return gen()

    class FakeChat:
        def __init__(self):
            self.completions = FakeCompletions()

    class FakeAsyncGroq:
        def __init__(self, *, api_key):
            self.api_key = api_key
            self.chat = FakeChat()

    fake_module = types.ModuleType("groq")
    fake_module.AsyncGroq = FakeAsyncGroq
    monkeypatch.setitem(sys.modules, "groq", fake_module)

    llm = CloudLLM(api_key="test-key")
    chunks = asyncio.run(_collect(llm.stream([{"role": "user", "content": "hi"}])))

    assert chunks == ["Hi"]
    kwargs = captured["kwargs"]
    assert kwargs["stream"] is True
    assert "stream_options" not in kwargs, "groq 1.x rejects stream_options — sending it breaks every cloud stream"
    assert set(kwargs) == {"model", "messages", "temperature", "max_tokens", "stream"}


def test_local_stream_times_out_on_stalled_chunk(monkeypatch):
    """A stalled Ollama (no data within chunk_timeout) must raise so the
    router can fall back to cloud — not block for the full request_timeout.

    The fake's iter_lines blocks until released, simulating a server that
    accepted the request but stopped sending bytes.
    """
    import threading
    import time

    release = threading.Event()

    class FakeResponse:
        def raise_for_status(self):
            return None

        def iter_lines(self):
            release.wait(timeout=30)
            return iter([])

    class FakeCM:
        def __enter__(self):
            return FakeResponse()

        def __exit__(self, *exc):
            return False

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def stream(self, method, url, **kwargs):
            return FakeCM()

    monkeypatch.setattr("llm.local.httpx.Client", FakeClient)
    # first_chunk_timeout must also be small: the stalled chunk IS the first
    # one, and cold-load allowance must not swallow the stall.
    llm = LocalLLM(chunk_timeout=0.3, first_chunk_timeout=0.3)

    async def run():
        try:
            async for _ in llm.stream([{"role": "user", "content": "hi"}]):
                pass
            raised = False
        except asyncio.TimeoutError:
            raised = True
        finally:
            # Unblock the abandoned worker thread BEFORE asyncio.run shuts
            # down the default executor (which joins its threads).
            release.set()
            await asyncio.sleep(0.2)
        return raised

    # Verify the caller-visible stall is bounded by chunk_timeout (0.3s), not
    # request_timeout (300s) — the whole point of the fix.
    started = time.monotonic()
    assert asyncio.run(run()) is True, "a stalled chunk must raise TimeoutError"
    elapsed = time.monotonic() - started
    assert elapsed < 5.0, f"stall took {elapsed:.1f}s — chunk timeout not enforced"

def test_local_stream_first_chunk_gets_cold_load_allowance(monkeypatch):
    """A slow FIRST chunk (cold model load) must not trip the steady-state
    chunk_timeout — it gets first_chunk_timeout instead."""
    import time

    state = {"first": True}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def iter_lines(self):
            if state["first"]:
                state["first"] = False
                time.sleep(1.0)  # cold model load before the first token
            return iter([
                '{"message": {"content": "Hel"}, "done": false}',
                '{"message": {"content": "lo"}, "done": true, "model": "qwen3:4b"}',
            ])

    class FakeCM:
        def __enter__(self):
            return FakeResponse()

        def __exit__(self, *exc):
            return False

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def stream(self, method, url, **kwargs):
            return FakeCM()

    monkeypatch.setattr("llm.local.httpx.Client", FakeClient)
    # chunk_timeout (0.2s) is far tighter than the 1s cold load; only the
    # first-chunk allowance (5s) lets the stream survive.
    llm = LocalLLM(chunk_timeout=0.2, first_chunk_timeout=5.0)

    chunks = asyncio.run(_collect(llm.stream([{"role": "user", "content": "hi"}])))
    assert chunks == ["Hel", "lo"]



def test_local_stream_closes_response_when_abandoned_mid_stream(monkeypatch):
    """Early exit (the router acloses the async generator on failure/fallback)
    must close the sync generator, so the `with client.stream(...)` unwinds and
    the Ollama response releases its pooled connection promptly — not when the
    abandoned generator happens to be garbage collected."""
    lines = [
        '{"message": {"content": "Hel"}, "done": false}',
        '{"message": {"content": "lo"}, "done": true, "model": "qwen3:5.4b"}',
    ]

    class FakeResponse:
        def __init__(self):
            self.closed = False

        def raise_for_status(self):
            return None

        def iter_lines(self):
            return iter(lines)

    class FakeCM:
        def __init__(self, response):
            self._response = response

        def __enter__(self):
            return self._response

        def __exit__(self, *exc):
            self._response.closed = True
            return False

    class FakeClient:
        def __init__(self, *args, **kwargs):
            self._response = FakeResponse()

        def stream(self, method, url, **kwargs):
            return FakeCM(self._response)

    monkeypatch.setattr("llm.local.httpx.Client", FakeClient)
    llm = LocalLLM()

    async def run():
        agen = llm.stream([{"role": "user", "content": "hi"}])
        first = await anext(agen)
        assert first == "Hel"
        await agen.aclose()  # abandon mid-stream, like the router's fallback
        return llm._client._response.closed

    assert asyncio.run(run()) is True, \
        "abandoning the async generator must release the HTTP response"


def test_local_stream_closes_response_after_chunk_timeout(monkeypatch):
    """After a per-chunk timeout the worker thread may still be inside
    next(gen) (blocked on the read).  Once it unblocks it must notice the
    async side gave up and close the generator itself, releasing the response.

    The fake stalls past the deadline, then sends a chunk — the exact moment
    the old code abandoned the generator suspended at a yield inside the open
    `with client.stream(...)` block, leaking the connection until GC."""
    import time

    class FakeResponse:
        def __init__(self):
            self.closed = False

        def raise_for_status(self):
            return None

        def iter_lines(self):
            time.sleep(0.15)  # stall past the deadline, then respond
            return iter(['{"message": {"content": "late"}, "done": false}'])

    class FakeCM:
        def __init__(self, response):
            self._response = response

        def __enter__(self):
            return self._response

        def __exit__(self, *exc):
            self._response.closed = True
            return False

    class FakeClient:
        def __init__(self, *args, **kwargs):
            self._response = FakeResponse()

        def stream(self, method, url, **kwargs):
            return FakeCM(self._response)

    monkeypatch.setattr("llm.local.httpx.Client", FakeClient)
    llm = LocalLLM(chunk_timeout=0.02, first_chunk_timeout=0.02)

    async def run():
        try:
            async for _ in llm.stream([{"role": "user", "content": "hi"}]):
                pass
            raised = False
        except asyncio.TimeoutError:
            raised = True
        # Let the abandoned worker thread unblock and run its close.
        for _ in range(200):
            if llm._client._response.closed:
                break
            await asyncio.sleep(0.01)
        return raised, llm._client._response.closed

    raised, closed = asyncio.run(run())
    assert raised is True, "stall must surface TimeoutError"
    assert closed is True, "the timed-out worker must close the response"


def test_local_stream_raises_on_ollama_http200_error_body(monkeypatch):
    """Ollama surfaces subscription/rate-limit failures in STREAMING responses
    as an HTTP 200 body {\"error\": ...}. The stream must RAISE (so the router
    can fall back to the local model) instead of silently yielding nothing."""
    import pytest

    lines = [
        '{"error": "this model requires a subscription, upgrade for access: https://ollama.com/upgrade (ref: abc123)"}',
    ]

    class FakeResponse:
        def raise_for_status(self):
            return None  # HTTP 200 — the error lives in the body, not the status

        def iter_lines(self):
            return iter(lines)

    class FakeCM:
        def __enter__(self):
            return FakeResponse()

        def __exit__(self, *exc):
            return False

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def stream(self, method, url, **kwargs):
            return FakeCM()

        def close(self):
            pass

    monkeypatch.setattr("llm.local.httpx.Client", FakeClient)
    llm = LocalLLM()

    async def run():
        with pytest.raises(RuntimeError, match="requires a subscription"):
            await _collect(llm.stream([{"role": "user", "content": "hi"}]))
        await llm.close()

    asyncio.run(run())
