"""Text-to-speech — Deepgram only."""

from __future__ import annotations

import base64
import os
import threading
from typing import Any, Optional

import httpx


class TTSEngine:
    def __init__(self, settings: Any) -> None:
        self.settings = settings
        # Try to get from settings first, then fallback to environment
        self.api_key = getattr(settings, "deepgram_api_key", None)
        if not self.api_key:
            self.api_key = os.getenv("DEEPGRAM_API_KEY")
        self.model = getattr(settings, "deepgram_tts_model", "aura-asteria-en")
        self.voice = getattr(settings, "deepgram_tts_voice", getattr(settings, "tts_voice", "aura-asteria-en"))
        self._client: Optional[httpx.AsyncClient] = None
        self._client_lock = threading.Lock()

    def available(self) -> bool:
        return bool(self.api_key)

    def _get_client(self) -> httpx.AsyncClient:
        """Lazily create the pooled HTTP client (one per engine, created in the
        calling event loop so the connection pool stays loop-bound)."""
        if self._client is None:
            with self._client_lock:
                if self._client is None:
                    self._client = httpx.AsyncClient(timeout=60.0)
        return self._client

    async def close(self) -> None:
        """Close the pooled client. Idempotent — safe to call twice."""
        with self._client_lock:
            client, self._client = self._client, None
        if client is not None:
            await client.aclose()

    async def synthesize(self, text: str, voice: Optional[str] = None) -> bytes:
        if not self.api_key:
            raise RuntimeError("Deepgram not available — set DEEPGRAM_API_KEY")
        return await self._deepgram_synthesize(text, voice=voice)

    async def _deepgram_synthesize(self, text: str, voice: Optional[str] = None) -> bytes:
        client = self._get_client()
        final_voice = voice or self.voice
        response = await client.post(
            "https://api.deepgram.com/v1/speak",
            params={"model": self.model, "voice": final_voice},
            headers={
                "Authorization": f"Token {self.api_key}",
                "Accept": "audio/*",
            },
            json={"text": text},
        )
        response.raise_for_status()
        return response.content

    async def synthesize_base64(self, text: str, voice: Optional[str] = None) -> tuple[str, str]:
        data = await self.synthesize(text, voice=voice)
        return base64.b64encode(data).decode(), "audio/mpeg"
