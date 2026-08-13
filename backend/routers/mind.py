"""Living Mind endpoints — a read-only window into Emma's mind.

- GET /api/mind-map          — the skeleton: regions, nodes, edges, stats
- GET /api/mind-map/node/{id}— lazy full detail for one node (404 unknown)
- WS  /ws/observe            — read-only live event stream for observers

The observer socket is a spectator: it never touches the chat/voice
connection state, sends are timeout-guarded and pruned by the bus, and it
is gated to same-origin clients.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect

from agents.control import ControlAgent
from mind.assemble import assemble_mind_map, resolve_node_detail
from mind.events import mind_bus

log = logging.getLogger(__name__)

router = APIRouter(tags=["mind"])

# Maximum episodes pulled for the skeleton — 201 exist today; cap keeps a
# pathological memory store from stalling the map.
EPISODE_FETCH_LIMIT = 5000


def _core_name(pipeline) -> str:
    return str(getattr(pipeline.settings, "app_name", "emma") or "emma").lower()


def _gather_episodes(pipeline) -> list[dict]:
    return list(pipeline.episodic.recent(limit=EPISODE_FETCH_LIMIT))


def _gather_agents(pipeline) -> list[dict]:
    registry = getattr(pipeline, "agent_registry", None)
    if registry is None:
        return []
    return [m.to_dict() for m in registry.all_agents().values()]


def _gather_knowledge(pipeline) -> list[dict]:
    """Always-loaded knowledge files — real, manifest-scoped, readable."""
    files: list[dict] = []
    data_dir = getattr(pipeline.settings, "data_dir", None)
    if data_dir is not None:
        agents_dir = Path(data_dir) / "agents"
        if agents_dir.exists():
            for p in sorted(list(agents_dir.glob("*.yaml")) + list(agents_dir.glob("*.yml"))):
                try:
                    preview = p.read_text(encoding="utf-8", errors="replace")[:200]
                except OSError:
                    preview = ""
                files.append({
                    "id": f"manifest-{p.stem}",
                    "label": f"manifest: {p.stem}",
                    "path": str(p),
                    "preview": preview,
                })
    dossiers = Path(__file__).resolve().parent.parent.parent / "board" / "dossiers"
    if dossiers.exists():
        for p in sorted(dossiers.glob("*.md")):
            try:
                preview = p.read_text(encoding="utf-8", errors="replace")[:200]
            except OSError:
                preview = ""
            files.append({
                "id": f"dossier-{p.stem}",
                "label": f"dossier: {p.stem}",
                "path": str(p),
                "preview": preview,
            })
    try:
        prompt = pipeline.reasoning._system_prompt()
        files.append({
            "id": "system-prompt",
            "label": "System Prompt",
            "path": "agents/reasoning.py",
            "preview": (prompt or "")[:200],
        })
    except Exception:
        pass
    return files


@router.get("/api/mind-map")
async def mind_map(request: Request):
    pipeline = request.app.state.pipeline
    episodes: list[dict] = []
    fetch_error = None
    try:
        episodes = _gather_episodes(pipeline)
    except Exception as exc:
        fetch_error = f"{type(exc).__name__}: {str(exc)[:120]}"
        log.warning("mind: episode fetch failed (%s) — memory region will report error", fetch_error)

    agents = _gather_agents(pipeline)
    tools = dict(ControlAgent.TOOL_CATALOG)
    knowledge = _gather_knowledge(pipeline)
    core = _core_name(pipeline)

    data = assemble_mind_map(episodes, agents, tools, knowledge, core_name=core)
    if fetch_error:
        data["stats"]["sources"]["memory"] = "error"
        data["stats"]["memory_error"] = fetch_error
    return data


@router.get("/api/mind-map/node/{node_id}")
async def mind_node(node_id: str, request: Request):
    pipeline = request.app.state.pipeline
    episodes = _gather_episodes(pipeline)
    agents = _gather_agents(pipeline)
    tools = dict(ControlAgent.TOOL_CATALOG)
    knowledge = _gather_knowledge(pipeline)
    core = _core_name(pipeline)

    detail = resolve_node_detail(node_id, episodes, agents, tools, knowledge, core_name=core)
    if detail is None:
        raise HTTPException(status_code=404, detail="unknown node")
    return detail


# ------------------------------------------------------------ observer WS
def _same_origin(websocket: WebSocket) -> bool:
    """Allow same-host browsers only; non-browser clients (no Origin) pass."""
    host = websocket.headers.get("host", "")
    origin = websocket.headers.get("origin", "")
    if not origin:
        return True
    try:
        o = urlparse(origin)
    except ValueError:
        return False
    hostname = o.hostname or ""
    if not hostname:
        return False
    return hostname == host.split(":")[0] or host.startswith(hostname)


@router.websocket("/ws/observe")
async def ws_observe(websocket: WebSocket):
    if not _same_origin(websocket):
        await websocket.close(code=1008)
        return
    await websocket.accept()
    await mind_bus.register(websocket)
    try:
        while True:
            # Observers are read-only: drain and ignore anything sent.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        await mind_bus.unregister(websocket)
