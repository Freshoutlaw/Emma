"""Chat router — the main agent interaction endpoint (JSON + SSE streaming).

OPTIMIZATIONS:
- Fast JSON serialization with orjson fallback
- Better streaming performance
- Response compression handled at middleware level
"""

from __future__ import annotations

import base64
import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from agents.router import Pipeline

# Try to use orjson for faster JSON serialization
try:
    import orjson
    def json_dumps(obj):
        # orjson.dumps returns bytes — decode so the SSE data: line is valid JSON.
        return orjson.dumps(obj, default=str).decode("utf-8")
except ImportError:
    json_dumps = json.dumps

router = APIRouter(prefix="/api/chat", tags=["chat"])


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    session_id: str | None = None


class VisionRequest(BaseModel):
    """The 'describe this image' flow: a message (optional) plus an image as a
    base64 data URL ('data:image/png;base64,...') or raw base64."""

    message: str = Field(default="", max_length=8000)
    image: str = Field(min_length=1, max_length=30_000_000)


class VisionLiveRequest(BaseModel):
    """Live-watch mode: Emma re-analyzes a changing image source every
    ``interval_seconds`` and reports when the scene changes.  ``image`` is a
    base64 data URL / raw base64 (a static frame) or a plain http(s) URL to a
    live-updating image (webcam, monitoring feed) that is re-fetched each
    frame.  ``source`` selects what gets watched: ``image`` (attached image /
    URL), ``screen`` (desktop screenshots) or ``browser`` (headless-browser
    screenshots).  ``min_change_interval`` throttles change reports so a busy
    screen doesn't make Emma chatter ("only when needed")."""

    message: str = Field(default="", max_length=8000)
    image: str = Field(default="", max_length=30_000_000)
    interval_seconds: float = Field(default=5.0, ge=1.0, le=60.0)
    max_seconds: int = Field(default=600, ge=10, le=3600)
    source: str = Field(default="image", pattern="^(image|screen|browser)$")
    min_change_interval: float = Field(default=15.0, ge=0.0, le=300.0)


def decode_image_payload(payload: str) -> bytes:
    """Decode an image sent as a data URL or raw base64; validate it is a
    PNG/JPEG.  Raises ValueError with a user-facing message on bad input."""
    b64 = payload.strip()
    if "," in b64 and b64.startswith("data:"):
        b64 = b64.split(",", 1)[1]
    try:
        data = base64.b64decode(b64, validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"invalid base64 image payload: {exc}") from exc
    if not data:
        raise ValueError("empty image payload")
    if not (data.startswith(b"\x89PNG") or data.startswith(b"\xff\xd8\xff")):
        raise ValueError("unsupported image type — send a PNG or JPEG")
    return data


def _pipeline(request: Request) -> Pipeline:
    return request.app.state.pipeline


@router.post("")
async def chat_endpoint(body: ChatRequest, request: Request):
    """Run the agent pipeline and return the structured result."""
    pipeline = _pipeline(request)
    result = await pipeline.router.run(body.message, session_id=body.session_id)
    if result.pending_consent:
        return JSONResponse(
            status_code=409,
            content={"detail": "consent required", "decision": result.pending_consent},
        )
    if not result.ok and result.error:
        return JSONResponse(status_code=500, content={"detail": result.error, "result": result.to_dict()})
    return result.to_dict()


@router.get("/stream")
async def chat_stream(request: Request, message: str, session_id: str | None = None):
    """Server-sent-events stream: token / action / consent / memory / done events."""
    pipeline = _pipeline(request)

    async def event_gen():
        async for event in pipeline.router.stream(message):
            yield f"data: {json_dumps(event)}\n\n"

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/vision")
async def chat_vision(body: VisionRequest, request: Request):
    """SSE stream: the user shares an image (base64) through the HUD and Emma
    describes it with the vision-capable model.  Events match /api/chat/stream
    (token / speak_segment / memory / done)."""
    pipeline = _pipeline(request)
    try:
        image_bytes = decode_image_payload(body.image)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    async def event_gen():
        async for event in pipeline.router.stream_vision(body.message, image_bytes):
            yield f"data: {json_dumps(event)}\n\n"

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/vision/live")
async def chat_vision_live(body: VisionLiveRequest, request: Request):
    """SSE stream for live-watch mode: Emma keeps analyzing the attached image
    (or image URL, or the screen/browser) every few seconds and reports when
    the scene changes.  Events: vision_start / vision_heartbeat /
    vision_change (with a speak_segment) / consent / vision_error /
    vision_stop."""
    pipeline = _pipeline(request)
    image: str | bytes = b""
    if body.source == "image":
        image = body.image
        if not body.image.strip().lower().startswith(("http://", "https://")):
            # Static frame — validate eagerly so bad payloads get a clean 400.
            try:
                image = decode_image_payload(body.image)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

    async def event_gen():
        async for event in pipeline.router.stream_vision_live(
            image,
            body.message,
            body.interval_seconds,
            body.max_seconds,
            source=body.source,
            min_change_interval=body.min_change_interval,
        ):
            yield f"data: {json_dumps(event)}\n\n"

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
