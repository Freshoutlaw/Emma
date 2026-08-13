"""Living Mind — backend guard tests.

Covers Tier 1 of the build:
- skeleton shape: flat namespaced nodes, flat edges, stats block
- similarity edges: real embeddings, top-3 above threshold, deduped,
  diagonal excluded, mixed embedding dimensions handled
- freshness decay and its floor
- recall trunks (top-degree, padded by freshness when sparse)
- region isolation: one broken source empties only that region
- lazy detail: neighbors, traversal guard on knowledge paths, 404-unknown
- the observer bus: fan-out, timeout-guarded pruning
"""

from __future__ import annotations

import asyncio

from mind.assemble import (
    assemble_mind_map,
    build_memory_web,
    freshness,
    resolve_node_detail,
)
from mind.events import MindBus


def _ep(ep_id, content, ts="2026-08-13T00:00:00+00:00", kind="user", vec=None):
    return {"id": ep_id, "ts": ts, "kind": kind, "content": content, "embedding": vec}


# ================================================================== skeleton
def test_skeleton_shape():
    data = assemble_mind_map([], [], {}, [], core_name="emma")
    assert set(data.keys()) == {"regions", "nodes", "edges", "stats"}
    assert {r["id"] for r in data["regions"]} == {"core", "memory", "working", "agents", "knowledge", "rim"}
    core = [n for n in data["nodes"] if n["type"] == "core"]
    assert core and core[0]["id"] == "agent:emma"
    assert core[0]["region"] == "core"


def test_node_ids_are_namespaced():
    episodes = [_ep("a1", "hello", vec=[1, 0]), _ep("a2", "world", vec=[0, 1])]
    data = assemble_mind_map(
        episodes,
        [{"name": "coder", "description": "writes code", "tool_allowlist": ["read_file"]}],
        {"read_file": {"description": "read", "args": {}}},
        [{"id": "system-prompt", "label": "System Prompt", "path": "x", "preview": "p"}],
    )
    ids = {n["id"] for n in data["nodes"]}
    assert any(i.startswith("mem:") for i in ids)
    assert "agent:coder" in ids
    assert "tool:read_file" in ids
    assert "know:system-prompt" in ids
    assert all(n["id"].count(":") == 1 for n in data["nodes"])


# ================================================================== similarity
def test_similarity_edges_top3_threshold_dedup():
    episodes = [
        _ep("e1", "the quick brown fox", vec=[1, 0, 0, 0]),
        _ep("e2", "a brown fox", vec=[0.9, 0.1, 0, 0]),
        _ep("e3", "taxes and filing", vec=[0, 0, 1, 0]),
        _ep("e4", "filing taxes properly", vec=[0, 0.1, 0.9, 0]),
    ]
    nodes, edges, stats = build_memory_web(episodes, "agent:emma")
    sim = [e for e in edges if e["kind"] == "similarity"]
    # e1-e2 and e3-e4 are similar; cross pairs are below threshold.
    pairs = {tuple(sorted((e["source"], e["target"]))) for e in sim}
    assert ("mem:e1", "mem:e2") in pairs
    assert ("mem:e3", "mem:e4") in pairs
    assert ("mem:e1", "mem:e3") not in pairs
    assert len(pairs) == len(sim)          # deduped — no duplicate pairs
    assert stats["sources"]["memory"] == "ok"


def test_similarity_excludes_self_and_subthreshold():
    episodes = [
        _ep("e1", "alpha", vec=[1, 0]),
        _ep("e2", "alpha again", vec=[0.99, 0.01]),
        _ep("e3", "unrelated", vec=[0, 1]),
    ]
    _, edges, _ = build_memory_web(episodes, "agent:emma")
    sim = [e for e in edges if e["kind"] == "similarity"]
    for e in sim:
        assert e["source"] != e["target"]
        assert e["weight"] > 0.35
    assert any(e["weight"] > 0.9 for e in sim)


def test_mixed_embedding_dimensions_do_not_crash():
    # 2 vectors @ 2-dim and 2 @ 3-dim — the matrix must be computed per
    # dimension group, never as one inhomogeneous array.
    episodes = [
        _ep("a1", "alpha", vec=[1, 0]),
        _ep("a2", "alpha two", vec=[0.99, 0.01]),
        _ep("b1", "beta", vec=[1, 0, 0]),
        _ep("b2", "beta two", vec=[0.98, 0.02, 0]),
    ]
    nodes, edges, stats = build_memory_web(episodes, "agent:emma")
    sim = [e for e in edges if e["kind"] == "similarity"]
    pairs = {tuple(sorted((e["source"], e["target"]))) for e in sim}
    assert ("mem:a1", "mem:a2") in pairs
    assert ("mem:b1", "mem:b2") in pairs
    assert ("mem:a1", "mem:b1") not in pairs
    assert len(nodes) == 4


# ================================================================== freshness
def test_freshness_decays_and_floors():
    assert freshness("2026-08-13T00:00:00+00:00") > 0.9      # today
    assert freshness("2025-01-01T00:00:00+00:00") == 0.15    # ancient → floor, never black
    assert 0 <= freshness("not-a-date") <= 1


# ================================================================== trunks
def test_trunks_follow_degree():
    episodes = [
        _ep("e1", "one", vec=[1, 0, 0]),
        _ep("e2", "one variant", vec=[0.95, 0.05, 0]),
        _ep("e3", "one variant b", vec=[0.9, 0.1, 0]),
        _ep("e4", "loner", vec=[0, 0, 1]),
    ]
    _, edges, _ = build_memory_web(episodes, "agent:emma")
    trunks = [e for e in edges if e["kind"] == "recall"]
    assert len(trunks) == 3
    assert all(e["target"] == "agent:emma" for e in trunks)
    # The loner must NOT be a trunk — the connected cluster owns recall.
    assert all("mem:e4" not in e["source"] for e in trunks)


def test_trunks_pad_with_freshest_when_sparse():
    # No similar pairs → trunks are the freshest memories, so recall always
    # visibly flows core-ward.
    episodes = [
        _ep("old", "old thought", ts="2024-01-01T00:00:00+00:00", vec=[1, 0]),
        _ep("fresh", "fresh thought", ts="2026-08-13T00:00:00+00:00", vec=[0, 1]),
    ]
    _, edges, _ = build_memory_web(episodes, "agent:emma")
    trunks = [e for e in edges if e["kind"] == "recall"]
    assert len(trunks) == 2
    assert {"mem:old", "mem:fresh"} == {e["source"] for e in trunks}


# ================================================================== isolation
def test_broken_memory_source_empties_only_memory(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("index gone")
    monkeypatch.setattr("mind.assemble.build_memory_web", boom)
    data = assemble_mind_map(
        [_ep("a", "x", vec=[1])],
        [{"name": "coder", "description": "c", "tool_allowlist": []}],
        {}, [{"id": "system-prompt", "label": "SP", "path": "x", "preview": ""}],
    )
    assert data["stats"]["sources"]["memory"] == "error"
    assert data["stats"]["sources"]["agents"] == "ok"
    ids = {n["id"] for n in data["nodes"]}
    assert "agent:coder" in ids          # the rest of the mind survives


# ================================================================== detail
def test_memory_detail_with_live_neighbors():
    episodes = [
        _ep("e1", "the quick brown fox", vec=[1, 0, 0]),
        _ep("e2", "a brown fox", vec=[0.9, 0.1, 0]),
        _ep("e3", "unrelated", vec=[0, 0, 1]),
    ]
    d = resolve_node_detail("mem:e1", episodes, [], {}, [])
    assert d is not None and d["type"] == "memory"
    assert d["neighbors"][0]["id"] == "mem:e2"
    assert all(nb["id"] != "mem:e1" for nb in d["neighbors"])   # no self-match


def test_unknown_detail_is_none():
    assert resolve_node_detail("mem:nope", [], [], {}, []) is None
    assert resolve_node_detail("bogus:id", [], [], {}, []) is None


def test_knowledge_traversal_guard():
    files = [{"id": "system-prompt", "label": "SP", "path": "agents/reasoning.py", "preview": "p"}]
    assert resolve_node_detail("know:system-prompt", [], [], {}, files) is not None
    # Only manifest ids are readable — never an arbitrary path.
    assert resolve_node_detail("know:/etc/passwd", [], [], {}, files) is None
    assert resolve_node_detail("know:../.env", [], [], {}, files) is None


def test_agent_and_tool_detail():
    agents = [{"name": "coder", "description": "writes code", "tool_allowlist": ["read_file"], "ollama_model": "qwen"}]
    tools = {"read_file": {"description": "read", "args": {"path": "str"}}}
    d = resolve_node_detail("agent:coder", [], agents, tools, [])
    assert d["tools"] == ["read_file"] and d["model"] == "qwen"
    t = resolve_node_detail("tool:read_file", [], [], tools, [])
    assert t["category"] == "files"
    assert resolve_node_detail("tool:rm_rf", [], [], tools, []) is None


# ================================================================== bus
def test_observer_bus_fans_out_and_prunes():
    class FakeWS:
        def __init__(self):
            self.sent = []
        async def send_text(self, text):
            self.sent.append(text)

    class DeadWS(FakeWS):
        async def send_text(self, text):
            raise ConnectionError("gone")

    bus = MindBus()
    ws = FakeWS()
    dead = DeadWS()
    asyncio.run(bus.register(ws))
    asyncio.run(bus.register(dead))
    asyncio.run(bus.publish({"type": "memory_recalled", "node_ids": ["mem:x"]}))
    assert len(ws.sent) == 1
    payload = ws.sent[0]
    assert '"memory_recalled"' in payload and "mem:x" in payload
    # The dead socket was pruned — it can never block a future publish.
    assert bus.observer_count() == 1
