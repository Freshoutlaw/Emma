"""Chat router — the main agent interaction endpoint (JSON + SSE streaming).

OPTIMIZATIONS:
- Fast JSON serialization with orjson fallback
- Better streaming performance
- Response compression handled at middleware level
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from agents.router import Pipeline

# Try to use orjson for faster JSON serialization
try:
    import orjson
    def json_dumps(obj):
        return orjson.dumps(obj, default=str)
except ImportError:
    json_dumps = json.dumps

router = APIRouter(prefix="/api/chat", tags=["chat"])


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    session_id: str | None = None


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
