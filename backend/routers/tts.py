"""TTS router — per-segment audio endpoint for the pipelined speak path.

GET /api/tts/<segment_id> looks up the segment text in the TTL'd store,
synthesizes it with edge-tts (streaming), and returns the audio bytes as
audio/mpeg.  The client fetches one segment at a time and plays them in
sequence — the first segment's audio arrives while the LLM is still
generating later sentences.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from backend.tts_store import tts_store
from interfaces.voice.tts import TTSEngine

router = APIRouter(prefix="/api/tts", tags=["tts"])


@router.get("/{segment_id}")
async def get_segment_audio(segment_id: str, request: Request):
    """Synthesize and return audio for a single sentence segment."""
    text = tts_store.get(segment_id)
    if text is None:
        raise HTTPException(status_code=404, detail=f"segment '{segment_id}' not found or expired")

    pipeline = request.app.state.pipeline
    tts: TTSEngine = pipeline.tts

    if not tts.available():
        raise HTTPException(status_code=503, detail="TTS engine not available (edge-tts not installed)")

    try:
        audio_bytes = await tts.synthesize(text)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"TTS synthesis failed: {exc}")

    return Response(
        content=audio_bytes,
        media_type="audio/mpeg",
        headers={
            "Cache-Control": "private, max-age=120",
            # X-Segment-Text removed due to HTTP header validation issues
        },
    )
