"""Speech-to-text — Deepgram only."""

from __future__ import annotations

import os
from typing import Any

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

    @property
    def deepgram_available(self) -> bool:
        return bool(self.api_key)

    async def transcribe(self, audio_bytes: bytes, mime: str = "audio/webm", language: str = "en") -> str:
        """Transcribe audio bytes using Deepgram."""
        if not self.deepgram_available:
            raise RuntimeError("Deepgram not available — set DEEPGRAM_API_KEY")
        return await self._deepgram_transcribe(audio_bytes, mime=mime, language=language)

    async def _deepgram_transcribe(self, audio_bytes: bytes, mime: str = "audio/webm", language: str = "en") -> str:
        async with httpx.AsyncClient(timeout=30.0) as client:
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
