import asyncio
import base64
import sys
import types

from interfaces.voice.stt import STTEngine
from interfaces.voice.tts import TTSEngine
from llm.cloud import CloudLLM


def test_cloud_client_factory_initializes_once(monkeypatch):
    class FakeAsyncGroq:
        def __init__(self, *, api_key):
            self.api_key = api_key

    fake_module = types.ModuleType("groq")
    fake_module.AsyncGroq = FakeAsyncGroq
    monkeypatch.setitem(sys.modules, "groq", fake_module)

    llm = CloudLLM(api_key="test-key")
    client = llm._client()

    assert isinstance(client, FakeAsyncGroq)
    assert client.api_key == "test-key"
    assert llm._groq_client is not None


def test_stt_engine_is_deepgram_only_without_api_key():
    """STT is Deepgram-only: without a key, transcribe raises instead of
    silently falling back to a local engine."""
    engine = STTEngine(settings=types.SimpleNamespace())
    assert engine.deepgram_available is False
    try:
        asyncio.run(engine.transcribe(b"audio-bytes", mime="audio/webm", language="en"))
        raised = False
    except RuntimeError as exc:
        raised = True
        assert "DEEPGRAM_API_KEY" in str(exc)
    assert raised is True


def test_deepgram_stt_transcribe_uses_api(monkeypatch):
    class FakeResponse:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            self.calls = []
            self.closed = False

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def aclose(self):
            self.closed = True

        async def post(self, url, params=None, headers=None, content=None):
            self.calls.append((url, params, headers, content))
            return FakeResponse({"results": {"channels": [{"alternatives": [{"transcript": "deepgram transcript"}]}]}})

    monkeypatch.setattr("httpx.AsyncClient", FakeAsyncClient)
    engine = STTEngine(
        settings=types.SimpleNamespace(deepgram_api_key="demo-key", deepgram_stt_model="nova-2"),
    )

    result = asyncio.run(engine.transcribe(b"audio-bytes", mime="audio/webm", language="en"))
    assert result == "deepgram transcript"


def test_deepgram_tts_synthesize_uses_api(monkeypatch):
    class FakeResponse:
        def __init__(self, payload):
            self.content = payload

        def raise_for_status(self):
            return None

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            self.calls = []
            self.closed = False

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def aclose(self):
            self.closed = True

        async def post(self, url, params=None, json=None, headers=None, **kwargs):
            self.calls.append((url, params, json, headers, kwargs))
            return FakeResponse(b"deepgram-audio")

    monkeypatch.setattr("httpx.AsyncClient", FakeAsyncClient)
    engine = TTSEngine(settings=types.SimpleNamespace(tts_voice="en-US-JennyNeural", deepgram_api_key="demo-key", deepgram_tts_model="aura-asteria-en", deepgram_tts_voice="aura-asteria-en"))

    payload = asyncio.run(engine.synthesize("hello"))
    assert payload == b"deepgram-audio"


def test_tts_synthesize_base64_returns_audio_payload(monkeypatch):
    class FakeResponse:
        def __init__(self, payload):
            self.content = payload

        def raise_for_status(self):
            return None

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            self.calls = []
            self.closed = False

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def aclose(self):
            self.closed = True

        async def post(self, url, params=None, json=None, headers=None, **kwargs):
            self.calls.append((url, params, json, headers, kwargs))
            return FakeResponse(b"deepgram-audio")

    monkeypatch.setattr("httpx.AsyncClient", FakeAsyncClient)
    engine = TTSEngine(settings=types.SimpleNamespace(
        tts_voice="en-US-JennyNeural",
        deepgram_api_key="demo-key",
        deepgram_tts_model="aura-asteria-en",
        deepgram_tts_voice="aura-asteria-en",
    ))
    data, mime = asyncio.run(engine.synthesize_base64("hello"))

    assert mime == "audio/mpeg"
    assert base64.b64decode(data) == b"deepgram-audio"

def test_stt_engine_pools_one_client_and_closes(monkeypatch):
    instances = []

    class FakeResponse:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            self.closed = False
            instances.append(self)

        async def aclose(self):
            self.closed = True

        async def post(self, url, params=None, headers=None, content=None):
            return FakeResponse({"results": {"channels": [{"alternatives": [{"transcript": "pooled transcript"}]}]}})

    monkeypatch.setattr("httpx.AsyncClient", FakeAsyncClient)
    engine = STTEngine(settings=types.SimpleNamespace(deepgram_api_key="demo-key", deepgram_stt_model="nova-2"))

    async def run():
        first = await engine.transcribe(b"a", mime="audio/wav")
        second = await engine.transcribe(b"b", mime="audio/wav")
        await engine.close()
        await engine.close()  # idempotent
        return first, second

    first, second = asyncio.run(run())
    assert first == second == "pooled transcript"
    assert len(instances) == 1, "one pooled client must serve every request"
    assert instances[0].closed is True
    assert engine._client is None


def test_tts_engine_pools_one_client_and_closes(monkeypatch):
    instances = []

    class FakeResponse:
        def __init__(self, payload):
            self.content = payload

        def raise_for_status(self):
            return None

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            self.closed = False
            instances.append(self)

        async def aclose(self):
            self.closed = True

        async def post(self, url, params=None, json=None, headers=None, **kwargs):
            return FakeResponse(b"deepgram-audio")

    monkeypatch.setattr("httpx.AsyncClient", FakeAsyncClient)
    engine = TTSEngine(settings=types.SimpleNamespace(
        deepgram_api_key="demo-key",
        deepgram_tts_model="aura-asteria-en",
        deepgram_tts_voice="aura-asteria-en",
    ))

    async def run():
        first = await engine.synthesize("hello")
        second = await engine.synthesize("world")
        await engine.close()
        return first, second

    first, second = asyncio.run(run())
    assert first == second == b"deepgram-audio"
    assert len(instances) == 1, "one pooled client must serve every request"
    assert instances[0].closed is True
    assert engine._client is None

