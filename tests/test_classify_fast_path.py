"""Classification fast path — short conversational messages must skip the
LLM intent call (one full round trip saved per greeting) and never fall
into the clarify-on-chat prompt that demanded "chat: hello world" prefixes.
"""

import asyncio
import types

from agents.router import AgentRouter


class _RecordingLLM:
    def __init__(self):
        self.calls = 0

    def route(self):
        return "local"

    async def complete(self, messages, temperature=0.0, max_tokens=200):
        self.calls += 1
        # A low-confidence "chat" guess: on the pre-fast-path code this made
        # maybe_clarify return a clarification prompt for short messages.
        return '{"intent": "chat", "confidence": 0.3}'


def _router(llm):
    pipeline = types.SimpleNamespace(llm=llm)
    return AgentRouter(pipeline)


def test_short_conversational_message_skips_llm_classify():
    llm = _RecordingLLM()
    router = _router(llm)

    result = asyncio.run(router.classify("hello world"))
    assert result["intent"] == "reasoning"
    assert llm.calls == 0, "short conversational messages must not call the LLM"


def test_longer_ambiguous_message_still_uses_llm_classify():
    llm = _RecordingLLM()
    router = _router(llm)

    result = asyncio.run(router.classify("tell me something interesting about the universe"))
    assert llm.calls == 1, "longer ambiguous messages still get LLM classification"
    assert result["intent"] == "chat"  # llm_result passed through the policy


def test_keyword_messages_never_call_llm_even_when_long():
    llm = _RecordingLLM()
    router = _router(llm)

    result = asyncio.run(router.classify("remember to buy milk and eggs tomorrow"))
    assert result["intent"] == "memory"
    assert llm.calls == 0
