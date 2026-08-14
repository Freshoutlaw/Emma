"""Assemble the Living Mind skeleton — a pure function, region-isolated.

One endpoint returns a flat list of nodes, a flat list of edges, and a
stats block. Each region is built inside its own ``try``: a broken memory
index or a failed query empties THAT region only and records
``"error"`` in the stats, instead of a 500 and a black page.

The similarity edges are the piece that matters: real embeddings from the
memory store, L2-normalized, full cosine matrix in numpy, top-3 neighbors
above 0.35, canonicalized and deduped. Node size grows with degree, and
the highest-degree memories get "recall" trunks back to the core.

Nothing here reaches into globals or imports the server — it takes plain
data (episodes, agent manifests, the tool catalog, knowledge files) so it
is testable without spinning anything up.
"""

from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timezone
from typing import Any, Optional

log = logging.getLogger(__name__)

# ---- load-bearing constants (tune these) --------------------------------
SIM_TOP_K = 3           # neighbors per memory — keeps the web readable
SIM_THRESHOLD = 0.35    # below this, memories are not "about the same thing"
MAX_MEMORIES = 400      # node population cap; freshest win
TRUNK_COUNT = 3         # high-degree memories hang the web off the core
WORKING_THREADS = 24    # recent conversations in the working cluster

FRESHNESS_HALF_LIFE_DAYS = 30.0
FRESHNESS_FLOOR = 0.15

REGION_COLORS = {
    "core": "#2DD4A8",
    "memory": "#A78BFA",
    "working": "#67E8F9",
    "agents": "#E88FB3",
    "knowledge": "#F5A524",
    "rim": "#8B93A1",
}


# ---------------------------------------------------------------- helpers
def freshness(ts: str) -> float:
    """Exponential decay on age — recent memories burn, old ones ember.
    0.5 ** (age_days / 30), floored at 0.15 so nothing goes fully black."""
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return 0.5
    now = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    age_days = max(0.0, (now - dt).total_seconds() / 86400.0)
    return max(FRESHNESS_FLOOR, 0.5 ** (age_days / FRESHNESS_HALF_LIFE_DAYS))


def _decode_embedding(raw: Any) -> Optional[list[float]]:
    if raw is None:
        return None
    if isinstance(raw, list):
        return raw
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None


def _label(content: str, limit: int = 64) -> str:
    text = (content or "").strip().replace("\n", " ")
    return text[:limit] + ("…" if len(text) > limit else "")


def _tool_category(name: str) -> str:
    if name in ("read_file", "write_file", "list_dir"):
        return "files"
    if name == "run_command":
        return "shell"
    if name in ("web_search", "fetch_page", "ollama_registry_search"):
        return "web"
    if name.startswith("git"):
        return "git"
    if name.startswith(("docker", "compose")):
        return "docker"
    if name.startswith("mqtt"):
        return "mqtt"
    if name.startswith("browser"):
        return "browser"
    if name.startswith("desktop"):
        return "desktop"
    return "misc"


# ---------------------------------------------------------------- memory web
def build_memory_web(episodes: list[dict], core_id: str) -> tuple[list[dict], list[dict], dict]:
    """Memory nodes + similarity edges + recall trunks. Returns
    (nodes, edges, stats_sources)."""
    nodes: list[dict] = []
    edges: list[dict] = []
    stats: dict[str, Any] = {}

    shown = episodes[:MAX_MEMORIES]
    stats["memory_total"] = len(episodes)
    stats["memory_shown"] = len(shown)

    # Decode vectors; keep indexable rows.
    rows: list[dict] = []
    vecs_by_idx: list[Optional[list[float]]] = []
    for ep in shown:
        vec = _decode_embedding(ep.get("embedding"))
        rows.append(ep)
        vecs_by_idx.append(vec)

    # ---- similarity matrix (numpy, trivial at this size) ----------------
    # Embeddings can have different dimensions if the embedding model ever
    # changed (104 vectors @ 768-dim, 98 @ 384-dim in this store). Cosine
    # across dimensions is meaningless, so similarity is computed WITHIN
    # each dimension group and the groups never mix.
    embedded = [(i, v) for i, v in enumerate(vecs_by_idx) if v]
    sim: dict[tuple[int, int], float] = {}
    if len(embedded) >= 2:
        try:
            import numpy as np
            by_dim: dict[int, list[int]] = {}
            for i, v in embedded:
                by_dim.setdefault(len(v), []).append(i)
            for dim, idxs in by_dim.items():
                if len(idxs) < 2:
                    continue
                mat = np.array([vecs_by_idx[i] for i in idxs], dtype=float)
                norms = np.linalg.norm(mat, axis=1, keepdims=True)
                norms[norms == 0] = 1.0
                unit = mat / norms
                s = unit @ unit.T
                np.fill_diagonal(s, -1.0)
                for a in range(len(idxs)):
                    order = np.argsort(-s[a])[:SIM_TOP_K]
                    for b in order:
                        score = float(s[a][b])
                        if score > SIM_THRESHOLD:
                            i, j = idxs[a], idxs[b]
                            key = (min(i, j), max(i, j))
                            sim[key] = max(sim.get(key, 0.0), score)
        except Exception as exc:
            log.warning("mind: similarity matrix failed (%s) — memory web without edges", str(exc)[:120])
            stats["memory_similarity"] = "error"

    # ---- degree → size, and trunk candidates ------------------------------
    degree: dict[int, int] = {}
    for (i, j) in sim:
        degree[i] = degree.get(i, 0) + 1
        degree[j] = degree.get(j, 0) + 1

    def trunk_key(idx: int) -> float:
        # highest degree first, then freshest
        return (degree.get(idx, 0), freshness(rows[idx].get("ts", "")))

    trunk_idxs = sorted(range(len(rows)), key=trunk_key, reverse=True)[:TRUNK_COUNT]

    # ---- nodes ------------------------------------------------------------
    for idx, ep in enumerate(rows):
        deg = degree.get(idx, 0)
        nodes.append({
            "id": f"mem:{ep.get('id')}",
            "type": "memory",
            "region": "memory",
            "label": _label(ep.get("content", "")),
            "color": REGION_COLORS["memory"],
            "size": 0.55 + 0.22 * min(deg, 3),
            "freshness": freshness(ep.get("ts", "")),
            "extra": {"kind": ep.get("kind", ""), "ts": ep.get("ts", ""), "source": ep.get("kind", "")},
        })

    # ---- edges -------------------------------------------------------------
    for (i, j), score in sim.items():
        edges.append({
            "source": f"mem:{rows[i].get('id')}",
            "target": f"mem:{rows[j].get('id')}",
            "kind": "similarity",
            "weight": round(score, 4),
        })
    for idx in trunk_idxs:
        edges.append({
            "source": f"mem:{rows[idx].get('id')}",
            "target": core_id,
            "kind": "recall",
            "weight": round(0.5 + 0.3 * min(degree.get(idx, 0), 3), 4),
        })

    stats["sources"] = stats.get("sources", {})
    stats["sources"]["memory"] = "ok"
    if not embedded:
        stats["sources"]["memory_edges"] = "no-embeddings"
    return nodes, edges, stats


# ---------------------------------------------------------------- regions
def _build_core(core_name: str) -> tuple[list[dict], list[dict]]:
    nodes = [{
        "id": f"agent:{core_name}",
        "type": "core",
        "region": "core",
        "label": core_name.capitalize(),
        "color": REGION_COLORS["core"],
        "size": 1.6,
        "freshness": 1.0,
        "extra": {"role": "the agent itself"},
    }]
    return nodes, []


def _build_working(episodes: list[dict], core_id: str) -> tuple[list[dict], list[dict]]:
    nodes, edges = [], []
    threads = [e for e in episodes if e.get("kind") == "user"][:WORKING_THREADS]
    for ep in threads:
        nodes.append({
            "id": f"thread:{ep.get('id')}",
            "type": "thread",
            "region": "working",
            "label": _label(ep.get("content", ""), 48),
            "color": REGION_COLORS["working"],
            "size": 0.5,
            "freshness": freshness(ep.get("ts", "")),
            "extra": {"ts": ep.get("ts", "")},
        })
        edges.append({
            "source": f"thread:{ep.get('id')}", "target": core_id,
            "kind": "recent", "weight": 0.25,
        })
    return nodes, edges


def _build_agents(agents: list[dict], core_id: str) -> tuple[list[dict], list[dict]]:
    nodes, edges = [], []
    for m in agents:
        name = m.get("name", "")
        if not name:
            continue
        allow = m.get("tool_allowlist") or []
        nodes.append({
            "id": f"agent:{name}",
            "type": "agent",
            "region": "agents",
            "label": name,
            "color": REGION_COLORS["agents"],
            "size": 1.05,
            "freshness": 1.0,
            "extra": {
                "description": m.get("description", ""),
                "tools": allow if isinstance(allow, list) else [],
                "model": m.get("ollama_model") or "",
            },
        })
        edges.append({"source": core_id, "target": f"agent:{name}", "kind": "dispatch", "weight": 0.6})
        for tool in allow:
            edges.append({
                "source": f"agent:{name}", "target": f"tool:{tool}",
                "kind": "tools", "weight": 0.45,
            })
    return nodes, edges


def _build_knowledge(files: list[dict], core_id: str) -> tuple[list[dict], list[dict]]:
    nodes, edges = [], []
    for f in files:
        nodes.append({
            "id": f"know:{f.get('id', '')}",
            "type": "knowledge",
            "region": "knowledge",
            "label": f.get("label", f.get("id", "")),
            "color": REGION_COLORS["knowledge"],
            "size": 0.7,
            "freshness": 1.0,
            "extra": {"path": f.get("path", ""), "preview": f.get("preview", "")[:140]},
        })
        edges.append({"source": f"know:{f.get('id', '')}", "target": core_id, "kind": "knowledge", "weight": 0.5})
    return nodes, edges


def _build_rim(tools: dict[str, dict], core_id: str) -> tuple[list[dict], list[dict]]:
    nodes, edges = [], []
    for name, spec in tools.items():
        nodes.append({
            "id": f"tool:{name}",
            "type": "tool",
            "region": "rim",
            "label": name,
            "color": REGION_COLORS["rim"],
            "size": 0.45,
            "freshness": 1.0,
            "extra": {
                "category": _tool_category(name),
                "description": (spec or {}).get("description", ""),
                "args": (spec or {}).get("args", {}),
            },
        })
    return nodes, edges


# ---------------------------------------------------------------- assemble
def assemble_mind_map(
    episodes: list[dict],
    agents: list[dict],
    tools: dict[str, dict],
    knowledge_files: list[dict],
    core_name: str = "emma",
) -> dict:
    """Assemble the full skeleton. Every region is isolated: a failure in
    one empties only that region and is recorded in stats, never a 500."""
    core_id = f"agent:{core_name}"
    regions = [
        {"id": r, "label": r.capitalize(), "color": c}
        for r, c in REGION_COLORS.items()
    ]
    nodes: list[dict] = []
    edges: list[dict] = []
    stats: dict[str, Any] = {"sources": {}}

    builders = {
        "core": lambda: _build_core(core_name),
        "memory": lambda: build_memory_web(episodes, core_id),
        "working": lambda: _build_working(episodes, core_id),
        "agents": lambda: _build_agents(agents, core_id),
        "knowledge": lambda: _build_knowledge(knowledge_files, core_id),
        "rim": lambda: _build_rim(tools, core_id),
    }
    for region, builder in builders.items():
        try:
            result = builder()
            rn, re_ = result[0], result[1]
            nodes.extend(rn)
            edges.extend(re_)
            if len(result) > 2:  # memory region returns stats too
                region_stats = result[2]
                stats["sources"].update(region_stats.get("sources", {}))
                stats["memory_total"] = region_stats.get("memory_total", stats.get("memory_total", 0))
                stats["memory_shown"] = region_stats.get("memory_shown", stats.get("memory_shown", 0))
            stats["sources"][region] = "ok"
        except Exception as exc:
            log.exception("mind: region '%s' failed (%s)", region, str(exc)[:120])
            stats["sources"][region] = "error"

    stats["memory_total"] = stats.get("memory_total", len(episodes))
    stats["memory_shown"] = stats.get("memory_shown", len(episodes))
    return {"regions": regions, "nodes": nodes, "edges": edges, "stats": stats}


# ---------------------------------------------------------------- detail
def resolve_node_detail(
    node_id: str,
    episodes: list[dict],
    agents: list[dict],
    tools: dict[str, dict],
    knowledge_files: list[dict],
    core_name: str = "emma",
) -> Optional[dict]:
    """Lazy full detail for one node. Only knowledge paths that were in the
    manifest are ever readable here — never an arbitrary path (traversal
    guard). Returns None for unknown ids."""
    prefix, _, rest = node_id.partition(":")

    if prefix == "mem":
        ep = next((e for e in episodes if e.get("id") == rest), None)
        if ep is None:
            return None
        # live similarity query: top-5 neighbors
        vec = _decode_embedding(ep.get("embedding"))
        neighbors: list[dict] = []
        if vec:
            scored: list[tuple[dict, float]] = []
            for other in episodes:
                if other.get("id") == rest:
                    continue
                ov = _decode_embedding(other.get("embedding"))
                if not ov or len(ov) != len(vec):
                    continue
                import math as _m
                d = _m.sqrt(sum((a - b) ** 2 for a, b in zip(vec, ov)) or 1e-9)
                cos = sum(a * b for a, b in zip(vec, ov)) / d
                scored.append((other, cos))
            scored.sort(key=lambda p: p[1], reverse=True)
            for other, cos in scored[:5]:
                neighbors.append({
                    "id": f"mem:{other.get('id')}",
                    "label": _label(other.get("content", ""), 48),
                    "score": round(float(cos), 4),
                })
        return {
            "id": node_id,
            "type": "memory",
            "body": ep.get("content", ""),
            "kind": ep.get("kind", ""),
            "ts": ep.get("ts", ""),
            "freshness": freshness(ep.get("ts", "")),
            "neighbors": neighbors,
        }

    if prefix == "thread":
        ep = next((e for e in episodes if e.get("id") == rest), None)
        if ep is None:
            return None
        return {"id": node_id, "type": "thread", "body": ep.get("content", ""), "ts": ep.get("ts", "")}

    if prefix == "agent":
        if rest == core_name:
            return {
                "id": node_id, "type": "core",
                "name": core_name.capitalize(),
                "description": "The agent itself — the star at the center of the mind.",
            }
        m = next((a for a in agents if a.get("name") == rest), None)
        if m is None:
            return None
        return {
            "id": node_id, "type": "agent",
            "name": m.get("name", ""),
            "description": m.get("description", ""),
            "tools": m.get("tool_allowlist") or [],
            "model": m.get("ollama_model") or "router default",
            "tags": m.get("tags", []),
        }

    if prefix == "tool":
        spec = tools.get(rest)
        if spec is None:
            return None
        return {
            "id": node_id, "type": "tool",
            "name": rest,
            "description": spec.get("description", ""),
            "args": spec.get("args", {}),
            "category": _tool_category(rest),
        }

    if prefix == "know":
        # Traversal guard: only ids that were in the manifest are readable.
        f = next((k for k in knowledge_files if k.get("id") == rest), None)
        if f is None:
            return None
        return {"id": node_id, "type": "knowledge", "path": f.get("path", ""), "preview": f.get("preview", "")}

    return None
