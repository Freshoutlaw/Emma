"""Speech-to-text — Deepgram only."""

from __future__ import annotations

import os
import threading
from typing import Any, Optional

import httpx


class STTEngine:
    def __init__(self, settings: Any) -> None:
        self.settings = settings
        # Try to get from settings first, then fallback to environment
        self.api_key = getattr(settings, "deepgram_api_key", None)
        if not self.api_key:
            self.api_key = os.getenv("DEEPGRAM_API_KEY")
        self.model = getattr(settings, "deepgram_stt_model", "nova-2")
        self._vosk_model: Any = None
        self._client: Optional[httpx.AsyncClient] = None
        self._client_lock = threading.Lock()

    @property
    def deepgram_available(self) -> bool:
        return bool(self.api_key)

    def _get_client(self) -> httpx.AsyncClient:
        """Lazily create the pooled HTTP client (one per engine, created in the
        calling event loop so the connection pool stays loop-bound)."""
        if self._client is None:
            with self._client_lock:
                if self._client is None:
                    self._client = httpx.AsyncClient(timeout=30.0)
        return self._client

    async def close(self) -> None:
        """Close the pooled client. Idempotent — safe to call twice."""
        with self._client_lock:
            client, self._client = self._client, None
        if client is not None:
            await client.aclose()

    async def transcribe(self, audio_bytes: bytes, mime: str = "audio/webm", language: str = "en") -> str:
        """Transcribe audio bytes using Deepgram."""
        if not self.deepgram_available:
            raise RuntimeError("Deepgram not available — set DEEPGRAM_API_KEY")
        return await self._deepgram_transcribe(audio_bytes, mime=mime, language=language)

    async def _deepgram_transcribe(self, audio_bytes: bytes, mime: str = "audio/webm", language: str = "en") -> str:
        client = self._get_client()
        # Deepgram works better with wav format, but accepts webm
        # Try with the provided mime type first
        try:
            response = await client.post(
                "https://api.deepgram.com/v1/listen",
                params={"model": self.model, "language": language},
                headers={
                    "Authorization": f"Token {self.api_key}",
                    "Content-Type": mime,
                },
                content=audio_bytes,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as e:
            # If webm fails, try with a different content type
            if "webm" in mime.lower():
                try:
                    response = await client.post(
                        "https://api.deepgram.com/v1/listen",
                        params={"model": self.model, "language": language},
                        headers={
                            "Authorization": f"Token {self.api_key}",
                            "Content-Type": "audio/wav",
                        },
                        content=audio_bytes,
                    )
                    response.raise_for_status()
                    payload = response.json()
                except Exception as e2:
                    raise RuntimeError(f"Deepgram transcription failed: {e2}") from e2
            else:
                raise RuntimeError(f"Deepgram transcription failed: {e}") from e

        channels = payload.get("results", {}).get("channels", [])
        for channel in channels:
            for alt in channel.get("alternatives", []):
                transcript = alt.get("transcript")
                if transcript:
                    return transcript.strip()
        return ""
