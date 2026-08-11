"""Security router — guardian status, consent approvals, kill switch, network gate."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from agents.router import Pipeline
from security.consent_manager import DEFAULT_RULES

router = APIRouter(prefix="/api/security", tags=["security"])


class TokenRequest(BaseModel):
    token: str


class ModeRequest(BaseModel):
    mode: str  # auto | once | strict


class KillSwitchRequest(BaseModel):
    engaged: bool
    reason: str = "operator"


class GateRequest(BaseModel):
    open: bool
    reason: str = "operator"


def _pipeline(request: Request) -> Pipeline:
    return request.app.state.pipeline


@router.get("/status")
async def security_status(request: Request):
    pipeline = _pipeline(request)
    return {
        "kill_switch": pipeline.kill_switch.is_engaged(),
        "kill_switch_reason": pipeline.kill_switch.reason(),
        "consent_mode": pipeline.consent.mode.value,
        "pending_consent": pipeline.consent.pending(),
        "network_gate": pipeline.network_gate.state,
        "rules": {action: severity for action, severity in DEFAULT_RULES.items()},
    }


@router.get("/audit")
async def audit_log(request: Request, limit: int = 50):
    pipeline = _pipeline(request)
    return pipeline.audit.recent(limit=min(limit, 500))


@router.post("/consent/approve")
async def consent_approve(body: TokenRequest, request: Request):
    pipeline = _pipeline(request)
    ok = pipeline.consent.approve(body.token)
    if not ok:
        raise HTTPException(status_code=404, detail="unknown or expired consent token")
    pipeline.audit.log("consent.approved", action="consent", actor="operator", detail={"token": body.token})
    return {"approved": True, "pending": pipeline.consent.pending()}


@router.post("/consent/deny")
async def consent_deny(body: TokenRequest, request: Request):
    pipeline = _pipeline(request)
    ok = pipeline.consent.deny(body.token)
    if not ok:
        raise HTTPException(status_code=404, detail="unknown or expired consent token")
    pipeline.audit.log("consent.denied", action="consent", actor="operator", detail={"token": body.token})
    return {"denied": True, "pending": pipeline.consent.pending()}


@router.post("/consent/mode")
async def consent_mode(body: ModeRequest, request: Request):
    pipeline = _pipeline(request)
    if body.mode not in ("auto", "once", "strict"):
        raise HTTPException(status_code=400, detail="mode must be one of: auto, once, strict")
    pipeline.consent.set_mode(body.mode)
    pipeline.audit.log("consent.mode_changed", action="consent_mode_change", actor="operator", detail={"mode": body.mode})
    return {"mode": body.mode}


@router.post("/killswitch")
async def killswitch(body: KillSwitchRequest, request: Request):
    pipeline = _pipeline(request)
    if body.engaged:
        pipeline.kill_switch.engage(reason=body.reason)
        pipeline.audit.log("kill_switch.engaged", action="kill_switch", actor="operator", detail={"reason": body.reason})
    else:
        pipeline.kill_switch.disengage()
        pipeline.audit.log("kill_switch.disengaged", action="kill_switch", actor="operator", detail={"reason": body.reason})
    return {"engaged": pipeline.kill_switch.is_engaged()}


@router.post("/network-gate")
async def network_gate(body: GateRequest, request: Request):
    pipeline = _pipeline(request)
    state = pipeline.network_gate.set(body.open, reason=body.reason)
    pipeline.audit.log("network_gate.toggled", action="network_gate_toggle", actor="operator", detail=state.to_dict())
    return state.to_dict()
