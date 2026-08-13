"""System router — status, command execution, processes, memory, activity."""

from __future__ import annotations

import time
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from agents.router import Pipeline
from capabilities.system_io import CommandError
from cost.usage import monthly_summary
from security.guardian import ConsentRequiredError

router = APIRouter(prefix="/api/system", tags=["system"])

# short in-memory cache so a UI that polls every minute is effectively free
_usage_cache: dict = {"at": 0.0, "payload": None}
_USAGE_CACHE_TTL = 60.0


class CommandRequest(BaseModel):
    command: str = Field(min_length=1)
    cwd: Optional[str] = None
    timeout: int = 120


def _pipeline(request: Request) -> Pipeline:
    return request.app.state.pipeline


@router.get("/status")
async def system_status(request: Request):
    """Full status: LLM route, services, security posture, memory stats."""
    pipeline = _pipeline(request)
    llm_route = pipeline.llm.route()
    try:
        supabase_reachable = await pipeline.supabase.health()
    except Exception:
        supabase_reachable = False
    # health() only proves the REST API answers — the HUD's SYNCED indicator
    # must mean Emma can actually write to and query the episodes table.
    supabase_schema: Optional[bool] = None
    if supabase_reachable:
        try:
            supabase_schema = await pipeline.supabase.schema_ok(
                embedding_dim=pipeline.settings.embedding_dim
            )
        except Exception:
            supabase_schema = None
    
    # Get model - it's synchronous now
    llm_model = pipeline.llm.model()
    
    return {
        "name": pipeline.settings.app_name,
        "version": pipeline.settings.version,
        "llm": {
            "route": llm_route,
            "model": llm_model,
            "ollama_url": pipeline.settings.ollama_url,
            "local_model": pipeline.settings.local_model,
            "cloud_model": pipeline.settings.cloud_model,
        },
        "services": {
            "ollama": pipeline.llm.is_local_available(),
            "groq": pipeline.llm.cloud.is_available(),
            "mqtt": pipeline.mqtt.status(),
            "supabase": {
                "configured": pipeline.supabase.is_configured(),
                "reachable": supabase_reachable,
                "schema": "ok" if supabase_schema is True
                          else ("missing" if supabase_schema is False else "unknown"),
            },
        },
        "security": {
            "kill_switch": pipeline.kill_switch.is_engaged(),
            "consent_mode": pipeline.consent.mode.value,
            "pending_consent": len(pipeline.consent.pending()),
            "network_gate": pipeline.network_gate.is_open,
        },
        "memory": {"episodes": pipeline.episodic.count()},
    }


@router.post("/command")
async def run_command(body: CommandRequest, request: Request):
    """Execute a shell command through the guardian-gated SystemIO."""
    pipeline = _pipeline(request)
    try:
        result = await pipeline.system_io.run_command(body.command, cwd=body.cwd, timeout=body.timeout)
    except ConsentRequiredError as exc:
        return JSONResponse(status_code=409, content={"detail": "consent required", "decision": exc.decision.to_dict()})
    except CommandError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return result.to_dict()


@router.get("/processes")
async def processes(request: Request):
    pipeline = _pipeline(request)
    result = await pipeline.system_io.run_command("ps aux | head -40")
    return {"stdout": result.stdout, "exit_code": result.exit_code}


@router.get("/memory/recent")
async def memory_recent(request: Request, limit: int = 20):
    pipeline = _pipeline(request)
    return pipeline.episodic.recent(limit=min(limit, 100))


@router.get("/activity")
async def activity(request: Request, limit: int = 30):
    pipeline = _pipeline(request)
    return pipeline.audit.recent(limit=min(limit, 200))


class DisplayRequest(BaseModel):
    panel: str | None = None  # "memory" | "status" | "guardian" | "map" | null (hide)
    reason: str = "operator request"
    payload: Optional[dict] = None  # e.g. region data for the map panel


@router.get("/usage")
async def usage(request: Request):
    """Month-to-date LLM cost dashboard payload (60s cached)."""
    now = time.monotonic()
    if _usage_cache["payload"] is not None and now - _usage_cache["at"] < _USAGE_CACHE_TTL:
        return _usage_cache["payload"]
    payload = monthly_summary(request.app.state.pipeline.usage_repo)
    _usage_cache.update(at=now, payload=payload)
    return payload


@router.get("/display")
async def display_state(request: Request):
    """Which HUD panel Emma wants shown right now (null = all hidden)."""
    return _pipeline(request).display.state()


@router.post("/display")
async def display_set(body: DisplayRequest, request: Request):
    """Show or hide a HUD panel on demand."""
    pipeline = _pipeline(request)
    pipeline.guardian.guard("display_toggle", {"panel": body.panel, "reason": body.reason})
    state = pipeline.display.set(body.panel, reason=body.reason, payload=body.payload)
    return state
