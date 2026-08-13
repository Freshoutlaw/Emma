"""Vision wiring — screenshot bytes must reach the LLM as image content.

Coverage:
- LocalLLM base64-encodes `images` into the Ollama payload (bytes are not
  JSON-serializable as-is).
- CloudLLM strips the `images` side-channel (Groq's model is text-only).
- The reasoning agent attaches screenshot bytes to the narration/synthesis
  user message, and retries text-only when the active model rejects images.
- ControlAgent.execute passes screenshot bytes through raw.
- The router stream captures screenshot bytes and narrates them.
- keyword_plan resolves screenshot asks deterministically (they are <=5
  words, so the LLM plan is skipped entirely).
"""

import asyncio
import base64
import types

from agents.control import ControlAgent, summarize_tool_output
from agents.reasoning import ReasoningAgent


PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64  # stand-in screenshot bytes
PNG_B64 = base64.b64encode(PNG).decode("ascii")


# ---------------------------------------------------------------- llm/local.py
def test_local_payload_base64_encodes_image_bytes():
    from llm.local import LocalLLM

    llm = LocalLLM()
    payload = llm._payload(
        [{"role": "user", "content": "what's on screen?", "images": [PNG]}],
        0.6,
        100,
        stream=False,
    )
    msg = payload["messages"][0]
    assert msg["images"] == [PNG_B64], "bytes must be base64 for the JSON payload"
    assert msg["content"] == "what's on screen?"
    assert "images" not in payload, "images belong on the message, not the payload"


def test_local_payload_passes_base64_strings_through():
    from llm.local import LocalLLM

    llm = LocalLLM()
    payload = llm._payload(
        [{"role": "user", "content": "hi", "images": ["already-encoded"]}],
        0.6,
        100,
        stream=False,
    )
    assert payload["messages"][0]["images"] == ["already-encoded"]


def test_local_complete_posts_images_base64(monkeypatch):
    """End-to-end: complete() must send the images as base64 strings so the
    httpx JSON body is serializable — raw bytes would raise on dumps."""
    from llm.local import LocalLLM

    class _FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"model": "gemma4:31b-cloud", "message": {"role": "assistant", "content": "I see a screen."}}

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            self.posts = []

        async def post(self, url, json=None, **kwargs):
            self.posts.append(json)
            return _FakeResponse()

        def close(self):
            pass

        async def aclose(self):
            pass

    fake = _FakeAsyncClient()
    monkeypatch.setattr("httpx.AsyncClient", lambda *a, **k: fake)
    llm = LocalLLM()

    async def run():
        try:
            return await llm.complete([{"role": "user", "content": "what's on screen?", "images": [PNG]}])
        finally:
            await llm.close()

    out = asyncio.run(run())
    assert out == "I see a screen."
    sent = fake.posts[0]["messages"][0]
    assert sent["images"] == [PNG_B64]


# ---------------------------------------------------------------- llm/cloud.py
def test_cloud_strips_images_from_messages():
    from llm.cloud import CloudLLM

    messages = CloudLLM._strip_images(
        [{"role": "user", "content": "what's on screen?", "images": [PNG_B64]}]
    )
    assert messages == [{"role": "user", "content": "what's on screen?"}]


# ---------------------------------------------------------------- reasoning
class _FakeLLM:
    """Records every messages list; streams/completes per behavior."""

    def __init__(self):
        self.stream_calls = []
        self.complete_calls = []
        self.stream_fail_with_images = None
        self.complete_fail_with_images = None

    async def stream(self, messages, temperature=0.7, max_tokens=4096):
        self.stream_calls.append(messages)
        has_images = any("images" in m for m in messages)
        if has_images and self.stream_fail_with_images:
            raise self.stream_fail_with_images
        for tok in ("I see ", "a screen."):
            yield tok

    async def complete(self, messages, temperature=0.7, max_tokens=4096):
        self.complete_calls.append(messages)
        has_images = any("images" in m for m in messages)
        if has_images and self.complete_fail_with_images:
            raise self.complete_fail_with_images
        return "I see a screen."


def _reasoning_agent(llm=None) -> ReasoningAgent:
    pipeline = types.SimpleNamespace(llm=llm or _FakeLLM())
    agent = ReasoningAgent.__new__(ReasoningAgent)
    agent.pipeline = pipeline
    return agent


def test_stream_narration_attaches_images_to_user_message():
    llm = _FakeLLM()
    agent = _reasoning_agent(llm)

    async def run():
        tokens = []
        async for token in agent.stream_narration(
            "look at my screen", "", ["[desktop_screenshot: screenshot captured]"], images=[PNG]
        ):
            tokens.append(token)
        return tokens

    assert asyncio.run(run()) == ["I see ", "a screen."]
    assert len(llm.stream_calls) == 1
    user_msg = llm.stream_calls[0][1]
    assert user_msg["images"] == [PNG], "raw bytes ride the message; LocalLLM encodes them"
    assert "look at my screen" in user_msg["content"]


def test_stream_narration_retries_without_images_when_model_rejects_them():
    """The active model may be text-only (e.g. the local fallback serving).
    A failure BEFORE any token must retry once without the image so the turn
    still answers instead of erroring out."""
    llm = _FakeLLM()
    llm.stream_fail_with_images = RuntimeError("model does not support images")
    agent = _reasoning_agent(llm)

    async def run():
        tokens = []
        async for token in agent.stream_narration("what's on my screen", "", ["[...]"], images=[PNG]):
            tokens.append(token)
        return tokens

    assert asyncio.run(run()) == ["I see ", "a screen."]
    assert len(llm.stream_calls) == 2, "first (with image) failed, second retried without"
    assert not any("images" in m for m in llm.stream_calls[1]), "retry must drop the image"


def test_synthesize_attaches_images_and_retries_text_only():
    llm = _FakeLLM()
    llm.complete_fail_with_images = RuntimeError("vision not supported by this model")
    agent = _reasoning_agent(llm)

    out = asyncio.run(agent._synthesize("what's on screen?", "", ["[...]"], images=[PNG]))
    assert out == "I see a screen."
    assert len(llm.complete_calls) == 2
    assert llm.complete_calls[0][1]["images"] == [PNG]
    assert not any("images" in m for m in llm.complete_calls[1])


def test_synthesize_without_images_single_call():
    llm = _FakeLLM()
    agent = _reasoning_agent(llm)

    out = asyncio.run(agent._synthesize("hello", "", []))
    assert out == "I see a screen."
    assert len(llm.complete_calls) == 1
    assert not any("images" in m for m in llm.complete_calls[0])


# ---------------------------------------------------------------- control
def test_execute_passes_screenshot_bytes_through():
    class _Cap:
        def __getattr__(self, name):
            return self.screenshot

        async def screenshot(self, path=None):
            return PNG

    pipeline = types.SimpleNamespace(
        audit=types.SimpleNamespace(log=lambda *a, **k: None),
        io=_Cap(), web=_Cap(), git=_Cap(), docker=_Cap(),
        mqtt=_Cap(), browser=_Cap(), desktop=_Cap(),
    )
    agent = ControlAgent.__new__(ControlAgent)
    agent.pipeline = pipeline
    agent.io = pipeline.io
    agent.web = pipeline.web
    agent.git = pipeline.git
    agent.docker = pipeline.docker
    agent.mqtt = pipeline.mqtt
    agent.browser = pipeline.browser
    agent.desktop = pipeline.desktop

    out = asyncio.run(agent.execute("desktop_screenshot", actor="reasoning"))
    assert out is PNG, "screenshot bytes must pass through raw for the vision model"


def test_summarize_tool_output_placeholder():
    note = summarize_tool_output("desktop_screenshot", PNG)
    assert note.startswith("[desktop_screenshot: screenshot captured")
    assert "shown to Emma's vision model" in note
    assert PNG.decode("latin1", "ignore") not in note, "no raw bytes in the terminal"


# ---------------------------------------------------------------- keyword plan
def test_keyword_plan_screenshot_rules():
    agent = _reasoning_agent()

    assert agent.keyword_plan("look at my screen") == [{"tool": "desktop_screenshot", "args": {}}]
    assert agent.keyword_plan("what's on my screen?") == [{"tool": "desktop_screenshot", "args": {}}]
    assert agent.keyword_plan("take a screenshot") == [{"tool": "desktop_screenshot", "args": {}}]
    assert agent.keyword_plan("browser screenshot") == [{"tool": "browser_screenshot", "args": {}}]
    # unrelated short asks must NOT trigger the screenshot path
    assert agent.keyword_plan("list files here") == [{"tool": "list_dir", "args": {"path": "."}}]
    assert agent.keyword_plan("hello") == []


# ---------------------------------------------------------------- router stream
def test_stream_captures_screenshot_bytes_and_narrates():
    from agents.router import AgentRouter

    captured = {}

    class _FakeAudit:
        def log(self, event, **kwargs):
            return None

    class _FakeLLM:
        def route(self):
            return "none"

    class _FakeRAG:
        async def augment(self, query, k):
            return ""

    class _FakeReasoning:
        async def plan(self, request, context=""):
            return [{"tool": "desktop_screenshot", "args": {}}]

        async def stream_narration(self, request, context, outputs, images=None):
            captured["images"] = images
            captured["outputs"] = outputs
            yield "I see a screen."

    class _FakeControl:
        async def execute(self, tool, actor=None, **args):
            return PNG

    class _FakeEpisodic:
        async def remember(self, content, kind="episode", payload=None):
            return "ep1"

    pipeline = types.SimpleNamespace(
        audit=_FakeAudit(),
        llm=_FakeLLM(),
        rag=_FakeRAG(),
        reasoning=_FakeReasoning(),
        control=_FakeControl(),
        episodic=_FakeEpisodic(),
    )
    router = AgentRouter(pipeline)

    async def run():
        return [e async for e in router.stream("look at my screen")]

    events = asyncio.run(run())
    assert captured["images"] == [PNG], "screenshot bytes must reach the narration"
    assert captured["outputs"] and "screenshot captured" in captured["outputs"][0]
    action = [e for e in events if e["type"] == "action"]
    assert action and isinstance(action[0]["action"]["output"], str)
    assert "shown to Emma's vision model" in action[0]["action"]["output"]
    tokens = "".join(e["text"] for e in events if e["type"] == "token")
    assert tokens == "I see a screen."


# ---------------------------------------------------------------- chat/vision endpoint
def test_decode_image_payload_accepts_data_url_and_raw():
    from backend.routers.chat import decode_image_payload
    import base64

    raw = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
    b64 = base64.b64encode(raw).decode("ascii")
    assert decode_image_payload(b64) == raw
    assert decode_image_payload("data:image/png;base64," + b64) == raw
    assert decode_image_payload("data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8\xff\xe0").decode()) == b"\xff\xd8\xff\xe0"


def test_decode_image_payload_rejects_bad_input():
    import pytest
    from backend.routers.chat import decode_image_payload

    with pytest.raises(ValueError, match="invalid base64"):
        decode_image_payload("not!!base64")
    with pytest.raises(ValueError, match="empty"):
        decode_image_payload("")
    with pytest.raises(ValueError, match="unsupported"):
        decode_image_payload("aGVsbG8=")  # valid base64, but not a PNG/JPEG


# ---------------------------------------------------------------- stream_vision
def test_stream_vision_narrates_the_shared_image():
    from agents.router import AgentRouter

    captured = {}

    class _FakeAudit:
        def log(self, event, **kwargs):
            return None

    class _FakeLLM:
        def route(self):
            return "none"

    class _FakeRAG:
        async def augment(self, query, k):
            return ""

    class _FakeReasoning:
        async def stream_narration(self, request, context, outputs, images=None):
            captured["request"] = request
            captured["images"] = images
            yield "This is "
            yield "a cat."

    class _FakeEpisodic:
        async def remember(self, content, kind="episode", payload=None):
            captured["remembered"] = content
            return "ep-vision"

    pipeline = types.SimpleNamespace(
        audit=_FakeAudit(),
        llm=_FakeLLM(),
        rag=_FakeRAG(),
        reasoning=_FakeReasoning(),
        episodic=_FakeEpisodic(),
    )
    router = AgentRouter(pipeline)

    async def run():
        return [e async for e in router.stream_vision("what animal is this?", PNG)]

    events = asyncio.run(run())
    assert captured["images"] == [PNG], "the shared image must reach the narration"
    assert "what animal is this?" in captured["request"]
    tokens = "".join(e["text"] for e in events if e["type"] == "token")
    assert tokens == "This is a cat."
    done = [e for e in events if e["type"] == "done"]
    assert done and done[0]["result"]["ok"] is True
    assert done[0]["result"]["intent"] == "vision"
    assert done[0]["result"]["actions"] == [], "vision turns plan no tools"
    assert captured["remembered"] == "what animal is this?"


def test_stream_vision_defaults_to_describe_this_image():
    from agents.router import AgentRouter

    captured = {}

    class _FakeAudit:
        def log(self, event, **kwargs):
            return None

    class _FakeLLM:
        def route(self):
            return "none"

    class _FakeRAG:
        async def augment(self, query, k):
            return ""

    class _FakeReasoning:
        async def stream_narration(self, request, context, outputs, images=None):
            captured["request"] = request
            captured["images"] = images
            yield "A red circle."

    class _FakeEpisodic:
        async def remember(self, content, kind="episode", payload=None):
            return "ep1"

    pipeline = types.SimpleNamespace(
        audit=_FakeAudit(),
        llm=_FakeLLM(),
        rag=_FakeRAG(),
        reasoning=_FakeReasoning(),
        episodic=_FakeEpisodic(),
    )
    router = AgentRouter(pipeline)

    async def run():
        return [e async for e in router.stream_vision("", PNG)]

    events = asyncio.run(run())
    assert captured["request"] == "Describe this image."
    assert captured["images"] == [PNG]
    tokens = "".join(e["text"] for e in events if e["type"] == "token")
    assert tokens == "A red circle."


# ---------------------------------------------------------------- vision live
def test_parse_scene_json():
    from agents.router import _parse_scene_json, _NO_VISION_MARKER
    assert _parse_scene_json('{"scene": "a dog", "changed": false}') == ("a dog", False)
    assert _parse_scene_json('{"scene": "a cat on a mat", "changed": true}') == ("a cat on a mat", True)
    assert _parse_scene_json('Sure! {"scene": "noisy", "changed": false}') == ("noisy", False)
    assert _parse_scene_json("I can't see images.") == (_NO_VISION_MARKER, False)
    assert _parse_scene_json("garbage") is None


def test_stream_vision_live_reports_change():
    from agents.router import AgentRouter

    calls = {"n": 0}
    captured = {}

    class _FakeAudit:
        def log(self, event, **kwargs):
            captured["audit"] = (event, (kwargs.get("detail") or {}).get("reason"))

    class _FakeLLM:
        last_served = None

        async def complete(self, messages, **kw):
            calls["n"] += 1
            assert messages[-1].get("images"), "the frame must ride the message as vision content"
            if calls["n"] == 1:
                return '{"scene": "an empty room", "changed": false}'
            if calls["n"] == 2:
                return '{"scene": "an empty room with a red ball in the center", "changed": true}'
            return '{"scene": "an empty room with a red ball in the center", "changed": false}'

    pipeline = types.SimpleNamespace(
        audit=_FakeAudit(), llm=_FakeLLM(), rag=None, reasoning=None, episodic=None,
    )
    router = AgentRouter(pipeline)

    async def run():
        events = []
        async for e in router.stream_vision_live(PNG, "", interval_seconds=0.01, max_seconds=3):
            events.append(e)
            if any(x["type"] == "vision_change" for x in events) and any(
                x["type"] == "vision_heartbeat" for x in events
            ):
                break
        return events

    events = asyncio.run(run())
    types_ = [e["type"] for e in events]
    assert types_[0] == "vision_start", "first frame establishes the baseline"
    assert "vision_change" in types_
    assert "vision_heartbeat" in types_, "unchanged frames must heartbeat"
    change = next(e for e in events if e["type"] == "vision_change")
    assert "red ball" in change["description"]
    assert any(e["type"] == "speak_segment" for e in events), "changes must be spoken"
    seg = next(e for e in events if e["type"] == "speak_segment")
    assert seg["is_final"] is True


def test_stream_vision_live_text_only_model_stops():
    from agents.router import AgentRouter

    class _FakeAudit:
        def log(self, event, **kwargs):
            return None

    class _FakeLLM:
        last_served = None

        async def complete(self, messages, **kw):
            return "I'm sorry, I'm a text-based model and can't see images."

    pipeline = types.SimpleNamespace(
        audit=_FakeAudit(), llm=_FakeLLM(), rag=None, reasoning=None, episodic=None,
    )
    router = AgentRouter(pipeline)

    async def run():
        return [e async for e in router.stream_vision_live(PNG, "", interval_seconds=0.01, max_seconds=2)]

    events = asyncio.run(run())
    errors = [e for e in events if e["type"] == "vision_error"]
    assert errors and "can't see" in errors[0]["message"]
    assert errors[0].get("retry") is False, "a text-only model can never see — no reconnect"
    # The error IS the terminal event — no trailing vision_stop after it.
    assert all(e["type"] != "vision_stop" for e in events)


def test_stream_vision_live_fetches_url_each_frame():
    from agents.router import AgentRouter

    fetches = {"n": 0}

    async def _fetcher(url):
        fetches["n"] += 1
        fetches["url"] = url
        return PNG

    class _FakeAudit:
        def log(self, event, **kwargs):
            return None

    class _FakeLLM:
        last_served = None

        async def complete(self, messages, **kw):
            return '{"scene": "a parking lot", "changed": false}'

    pipeline = types.SimpleNamespace(
        audit=_FakeAudit(), llm=_FakeLLM(), rag=None, reasoning=None, episodic=None,
    )
    router = AgentRouter(pipeline)

    async def run():
        events = []
        async for e in router.stream_vision_live(
            "https://cam.example.com/feed.jpg", "", interval_seconds=0.01, max_seconds=3, _fetcher=_fetcher
        ):
            events.append(e)
            if len([x for x in events if x["type"] == "vision_heartbeat"]) >= 3:
                break
        return events

    events = asyncio.run(run())
    assert fetches["url"] == "https://cam.example.com/feed.jpg"
    assert fetches["n"] >= 3, "the URL must be re-fetched every frame, not cached"
    assert events[0]["type"] == "vision_start"


def test_stream_vision_live_source_error_is_retryable():
    from agents.router import AgentRouter

    async def _fetcher(url):
        raise OSError("feed is down")

    class _FakeAudit:
        def log(self, event, **kwargs):
            return None

    class _FakeLLM:
        last_served = None

        async def complete(self, messages, **kw):
            return '{"scene": "a parking lot", "changed": false}'

    pipeline = types.SimpleNamespace(
        audit=_FakeAudit(), llm=_FakeLLM(), rag=None, reasoning=None, episodic=None,
    )
    router = AgentRouter(pipeline)

    async def run():
        return [e async for e in router.stream_vision_live(
            "https://cam.example.com/feed.jpg", "", interval_seconds=0.01, max_seconds=2, _fetcher=_fetcher
        )]

    events = asyncio.run(run())
    errors = [e for e in events if e["type"] == "vision_error"]
    assert errors and "could not load" in errors[0]["message"]
    assert errors[0].get("retry") is True, "a dead feed may come back — client should reconnect"
    assert all(e["type"] != "vision_stop" for e in events)


# ---------------------------------------------------------------- watch sources
def test_stream_vision_live_screen_source_captures_each_frame():
    from agents.router import AgentRouter

    shots = {"n": 0}

    class _FakeAudit:
        def log(self, event, **kwargs):
            return None

    class _FakeDesktop:
        async def screenshot(self, path=None):
            shots["n"] += 1
            return PNG

    class _FakeLLM:
        last_served = None

        async def complete(self, messages, **kw):
            return '{"scene": "a quiet desk", "changed": false}'

    pipeline = types.SimpleNamespace(
        audit=_FakeAudit(), llm=_FakeLLM(), rag=None, reasoning=None, episodic=None,
        desktop=_FakeDesktop(), browser=None,
    )
    router = AgentRouter(pipeline)

    async def run():
        events = []
        async for e in router.stream_vision_live(
            b"", "", interval_seconds=0.01, max_seconds=3, source="screen"
        ):
            events.append(e)
            if len([x for x in events if x["type"] == "vision_heartbeat"]) >= 3:
                break
        return events

    events = asyncio.run(run())
    assert shots["n"] >= 3, "screen source must capture a fresh screenshot every frame"
    assert events[0]["type"] == "vision_start"
    assert "vision_heartbeat" in [e["type"] for e in events]


def test_stream_vision_live_screen_consent_gate():
    from agents.router import AgentRouter
    from security.guardian import ConsentRequiredError

    class _FakeDecision:
        allow = False
        action = "desktop_control"
        severity = "MED"
        reason = "screenshot"
        token = "t-1"
        require_consent = True

        def to_dict(self):
            return {"allow": self.allow, "action": self.action, "severity": self.severity, "reason": self.reason, "token": self.token, "require_consent": self.require_consent}

    class _FakeAudit:
        def log(self, event, **kwargs):
            return None

    class _FakeDesktop:
        def __init__(self):
            self.calls = 0

        async def screenshot(self, path=None):
            self.calls += 1
            if self.calls == 1:
                raise ConsentRequiredError(_FakeDecision())
            return PNG

    class _FakeLLM:
        last_served = None

        async def complete(self, messages, **kw):
            return '{"scene": "a quiet desk", "changed": false}'

    desktop = _FakeDesktop()
    pipeline = types.SimpleNamespace(
        audit=_FakeAudit(), llm=_FakeLLM(), rag=None, reasoning=None, episodic=None,
        desktop=desktop, browser=None,
    )
    router = AgentRouter(pipeline)

    async def run():
        events = []
        async for e in router.stream_vision_live(
            b"", "", interval_seconds=0.01, max_seconds=3, source="screen"
        ):
            events.append(e)
            if any(x["type"] == "vision_start" for x in events):
                break
        return events

    events = asyncio.run(run())
    consents = [e for e in events if e["type"] == "consent"]
    assert consents and consents[0]["decision"]["token"] == "t-1"
    starts = [e for e in events if e["type"] == "vision_start"]
    assert starts, "the watch must resume once consent is granted"


def test_stream_vision_live_change_cooldown_suppresses_chatter():
    from agents.router import AgentRouter

    calls = {"n": 0}

    class _FakeAudit:
        def log(self, event, **kwargs):
            return None

    class _FakeLLM:
        last_served = None

        async def complete(self, messages, **kw):
            calls["n"] += 1
            if calls["n"] == 1:
                return '{"scene": "a white screen", "changed": false}'
            return '{"scene": "a white screen with a red X", "changed": true}'

    pipeline = types.SimpleNamespace(
        audit=_FakeAudit(), llm=_FakeLLM(), rag=None, reasoning=None, episodic=None,
        desktop=None, browser=None,
    )
    router = AgentRouter(pipeline)

    async def run():
        events = []
        async for e in router.stream_vision_live(
            PNG, "", interval_seconds=0.01, max_seconds=2,
            min_change_interval=3600,  # longer than the whole run -> at most one change
        ):
            events.append(e)
            if len([x for x in events if x["type"] == "vision_change"]) >= 1 and len(
                [x for x in events if x["type"] == "vision_heartbeat"]
            ) >= 2:
                break
        return events

    events = asyncio.run(run())
    changes = [e for e in events if e["type"] == "vision_change"]
    assert len(changes) == 1, "within the cooldown, repeated changes must not re-report"
    assert sum(1 for e in events if e["type"] == "speak_segment") == 1, "only one spoken change"
    assert any(e["type"] == "vision_heartbeat" for e in events)


def test_keyword_intent_watch_phrases():
    from agents.router import keyword_intent
    assert keyword_intent("watch my screen") == "watch_screen"
    assert keyword_intent("watch the browser") == "watch_browser"
    assert keyword_intent("keep an eye on my screen") == "watch_screen"
    assert keyword_intent("stop watching") == "watch_stop"
    assert keyword_intent("stop the watch") == "watch_stop"
    assert keyword_intent("watch a video with me") == "reasoning", "no false positive on generic watch"
