"""Cloud LLM provider — Groq API (llama-3.3-70b-versatile, whisper for STT).

The Groq client is created lazily so the whole system imports and runs even
without `GROQ_API_KEY` set — it only matters when the local provider is
unreachable.
"""

from __future__ import annotations

import os
from typing import Any, AsyncIterator, Optional

from cost.usage import record_usage
from llm.local import LLMUnavailable


class CloudLLM:
    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "llama-3.3-70b-versatile",
        whisper_model: str = "whisper-large-v3",
    ) -> None:
        self._api_key = api_key or os.getenv("GROQ_API_KEY")
        self.model = model
        self.whisper_model = whisper_model
        self._groq_client: Any = None

    # ---------------------------------------------------------------- health
    def is_available(self) -> bool:
        return bool(self._api_key)

    def _client(self) -> Any:
        if not self.is_available():
            raise LLMUnavailable("GROQ_API_KEY is not set")
        if self._groq_client is None:
            from groq import AsyncGroq

            self._groq_client = AsyncGroq(api_key=self._api_key)
        return self._groq_client

    # ---------------------------------------------------------------- chat
    @staticmethod
    def _strip_images(messages: list[dict]) -> list[dict]:
        """Groq's configured model (llama-3.3-70b-versatile) is text-only.

        Vision narration messages carry an `images` side-channel for Ollama;
        dropping it here lets the Groq fallback answer textually instead of
        choking on an unknown message field.
        """
        return [{k: v for k, v in m.items() if k != "images"} for m in messages]

    async def complete(self, messages: list[dict], temperature: float = 0.7, max_tokens: int = 4096) -> str:
        client = self._client()
        response = await client.chat.completions.create(
            model=self.model,
            messages=self._strip_images(messages),
            temperature=temperature,
            max_tokens=max_tokens,
        )
        # cost dashboard — best-effort capture, never breaks the turn
        record_usage(response.model or self.model, response.usage)
        return (response.choices[0].message.content or "") if response.choices else ""

    async def stream(self, messages: list[dict], temperature: float = 0.7, max_tokens: int = 4096) -> AsyncIterator[str]:
        client = self._client()
        stream = await client.chat.completions.create(
            model=self.model,
            messages=self._strip_images(messages),
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
            # NOTE: no stream_options — the installed groq client (1.x) does
            # not accept `stream_options`, which raised TypeError and broke
            # every cloud-streamed reply.  Usage capture for streaming is
            # best-effort; complete() still records full usage.
        )
        async for chunk in stream:
            if getattr(chunk, "usage", None):
                # final chunk — capture the totals
                record_usage(chunk.model or self.model, chunk.usage)
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta

    # ---------------------------------------------------------------- speech
    async def transcribe(self, audio: bytes, mime: str = "audio/webm", filename: str = "audio.webm") -> str:
        client = self._client()
        response = await client.audio.transcriptions.create(
            model=self.whisper_model,
            file=(filename, audio, mime),
        )
        return response.text
