"""Voice router — speech-to-text, agent response with TTS, and voice status."""

from __future__ import annotations

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from agents.router import Pipeline

router = APIRouter(prefix="/api/voice", tags=["voice"])


class VoiceRequest(BaseModel):
    text: str = Field(min_length=1, max_length=8000)
    synthesize: bool = True


def _pipeline(request: Request) -> Pipeline:
    return request.app.state.pipeline


@router.post("/transcribe")
async def transcribe(request: Request, file: UploadFile = File(...)):
    """Transcribe an uploaded audio file using Deepgram-first STT with fallbacks."""
    pipeline = _pipeline(request)
    data = await file.read()
    mime = file.content_type or "audio/webm"
    try:
        text = await pipeline.stt.transcribe(data, mime=mime)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"STT failed: {exc}") from exc
    return {"text": text, "mime": mime}


@router.post("/respond")
async def respond(body: VoiceRequest, request: Request):
    """Run the agent pipeline on spoken text and optionally synthesize speech."""
    pipeline = _pipeline(request)
    result = await pipeline.router.run(body.text)
    audio_base64 = None
    audio_mime = None
    if body.synthesize and pipeline.tts.available():
        try:
            audio_base64, audio_mime = await pipeline.tts.synthesize_base64(result.output)
        except Exception:
            pass  # TTS is best-effort — the text reply is still returned
    return {
        "reply": result.output,
        "intent": result.intent,
        "audio_base64": audio_base64,
        "audio_mime": audio_mime,
        "pending_consent": result.pending_consent,
    }


@router.get("/status")
async def voice_status(request: Request):
    pipeline = _pipeline(request)
    return {
        "stt": {
            "deepgram": pipeline.stt.deepgram_available,
            "cloud_groq": pipeline.llm.cloud.is_available(),
            "local_vosk": False,  # STT is Deepgram-only; kept for HUD compatibility
        },
        "tts": {
            "deepgram": bool(getattr(pipeline.tts, "api_key", None)),
            "available": pipeline.tts.available(),
        },
    }
