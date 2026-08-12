"""Tests for EpisodicMemory recall caching and query-cache pruning."""

import asyncio
import hashlib
from datetime import datetime

from memory.episodic import EpisodicMemory


class _FakeEmbedder:
    """Deterministic, offline embedder; counts calls."""

    def __init__(self):
        self.calls = 0

    async def embed(self, text):
        self.calls += 1
        digest = hashlib.blake2b(text.encode(), digest_size=8).digest()
        return [digest[i] / 255.0 for i in range(8)]


def _memory(tmp_path):
    mem = EpisodicMemory(
        db_path=str(tmp_path / "memory.db"),
        embedder=_FakeEmbedder(),
        supabase=None,
    )
    return mem


def test_recall_caches_results_by_query(tmp_path):
    mem = _memory(tmp_path)

    async def run():
        await mem.remember("the cat sat on the mat")
        await mem.remember("stocks rallied on monday")
        first = await mem.recall("cat mat", k=2)
        embeds_after_first = mem.embedder.calls
        second = await mem.recall("cat mat", k=2)
        embeds_after_second = mem.embedder.calls
        return first, second, embeds_after_first, embeds_after_second

    first, second, after_first, after_second = asyncio.run(run())
    assert first == second, "cached results should be identical"
    assert after_first == after_second, "cache hit should not re-embed"
    assert "cat mat:2" in mem._query_cache


def test_expired_cache_entry_is_recomputed(tmp_path):
    mem = _memory(tmp_path)

    async def run():
        await mem.remember("episode content one")
        await mem.recall("some query", k=2)
        embeds = mem.embedder.calls
        key = "some query:2"
        assert key in mem._query_cache
        # Age the entry past the TTL so the next recall must recompute.
        mem._query_cache[key]["timestamp"] = (
            datetime.now().timestamp() - mem._cache_ttl - 10
        )
        await mem.recall("some query", k=2)
        return embeds

    embeds = asyncio.run(run())
    assert mem.embedder.calls == embeds + 1, "expired entry should be re-embedded"


def test_query_cache_stays_bounded_after_many_distinct_queries(tmp_path):
    mem = _memory(tmp_path)

    async def run():
        await mem.remember("seed episode content")
        for i in range(230):
            await mem.recall(f"distinct query number {i}", k=2)
        return len(mem._query_cache)

    size = asyncio.run(run())
    assert size <= 201, f"query cache should stay bounded, was {size}"


def test_prune_removes_expired_entries(tmp_path):
    mem = _memory(tmp_path)

    async def run():
        await mem.remember("seed episode content")
        for i in range(200):
            await mem.recall(f"q{i}", k=2)
        # len is ~200; add a stale entry, then push past the prune threshold.
        mem._query_cache["stale"] = {
            "timestamp": datetime.now().timestamp() - 9999,
            "results": [],
        }
        for i in range(60):
            await mem.recall(f"more{i}", k=2)
        return "stale" in mem._query_cache

    stale_present = asyncio.run(run())
    assert stale_present is False, "expired cache entries must be pruned"


def test_recall_returns_decoded_rows_with_scores(tmp_path):
    mem = _memory(tmp_path)

    async def run():
        await mem.remember("the cat sat on the mat", payload={"k": 1})
        await mem.remember("the dog ran in the park", payload={"k": 2})
        rows = await mem.recall("cat mat", k=2)
        return rows

    rows = asyncio.run(run())
    assert len(rows) == 2
    scores = [r["score"] for r in rows]
    assert scores == sorted(scores, reverse=True), "results should be rank-ordered"
    assert sorted(r["payload"]["k"] for r in rows) == [1, 2], "payloads should round-trip"
    assert all(isinstance(r["score"], float) for r in rows)
    assert all(r["content"] for r in rows)


class _ControlledEmbedder:
    """Embedder with known cosine signs: 'pos*' -> +1 axis, 'neg*' -> -1 axis."""

    async def embed(self, text):
        if text.startswith("pos"):
            return [1.0, 0.0]
        if text.startswith("neg"):
            return [-1.0, 0.0]
        if text.startswith("query"):
            return [1.0, 0.0]
        return [0.0, 1.0]


def test_recall_fills_k_with_negative_cosine_rows_when_positives_scarce(tmp_path):
    """Regression: negative-cosine rows were dropped by the 0.0 pre-trim
    threshold, so recall returned fewer than k rows when positives were scarce."""
    mem = EpisodicMemory(
        db_path=str(tmp_path / "memory.db"),
        embedder=_ControlledEmbedder(),
        supabase=None,
    )

    async def run():
        await mem.remember("pos one")
        await mem.remember("neg one")
        await mem.remember("neg two")
        await mem.remember("neg three")
        return await mem.recall("query fill me", k=3)

    rows = asyncio.run(run())
    assert len(rows) == 3, "top-k must be filled even when few positives exist"
    assert [round(r["score"], 4) for r in rows] == [1.0, -1.0, -1.0]
    assert rows[0]["content"] == "pos one"


def test_recall_excludes_negatives_when_enough_positives_exist(tmp_path):
    mem = EpisodicMemory(
        db_path=str(tmp_path / "memory.db"),
        embedder=_ControlledEmbedder(),
        supabase=None,
    )

    async def run():
        await mem.remember("pos one")
        await mem.remember("pos two")
        await mem.remember("pos three")
        await mem.remember("neg one")
        await mem.remember("neg two")
        return await mem.recall("query only positives", k=2)

    rows = asyncio.run(run())
    assert len(rows) == 2
    assert all(round(r["score"], 4) == 1.0 for r in rows), "negatives must not displace positives"


def test_query_cache_prune_boundary_is_exact(tmp_path):
    """Pin the prune trigger: 201 entries is allowed, the 202nd insert drops
    the oldest half (201 // 2 = 100) and lands at 102."""
    mem = _memory(tmp_path)

    async def run():
        await mem.remember("seed episode content")
        for i in range(201):
            await mem.recall(f"q{i}", k=2)
        at_201 = len(mem._query_cache)
        await mem.recall("q201", k=2)  # the 202nd insert fires the prune
        at_202 = len(mem._query_cache)
        return at_201, at_202

    at_201, at_202 = asyncio.run(run())
    assert at_201 == 201, "below the threshold nothing may be pruned"
    assert at_202 == 102, "prune must drop the oldest half, then add the new entry"


def test_query_cache_prune_evicts_oldest_keeps_newest(tmp_path):
    """After sustained churn the oldest queries are gone and recent ones live."""
    mem = _memory(tmp_path)

    async def run():
        await mem.remember("seed episode content")
        for i in range(230):
            await mem.recall(f"distinct query number {i}", k=2)
        return len(mem._query_cache), "distinct query number 0:2" in mem._query_cache, "distinct query number 229:2" in mem._query_cache

    size, oldest_gone, newest_present = asyncio.run(run())
    # 201 -> drop 100 -> 101, then +29 inserts = 130 (see boundary test).
    assert size == 130, f"cache must settle at the post-prune steady state, was {size}"
    assert oldest_gone is False, "the oldest entries must be evicted"
    assert newest_present is True, "recent entries must survive"


class _FakeSupabase:
    """Fake Supabase that emulates server-side match_episodes filtering.

    If the caller sends a match_threshold, rows below it are dropped (mimicking
    a pgvector function that filters on the threshold); otherwise all rows are
    returned ordered as given. Records every rpc call for assertions.
    """

    def __init__(self, rows):
        self.rows = rows
        self.rpc_calls = []

    def is_configured(self):
        return True

    async def rpc(self, function, params):
        self.rpc_calls.append((function, dict(params or {})))
        results = self.rows
        threshold = (params or {}).get("match_threshold")
        if threshold is not None:
            results = [r for r in results if float(r.get("similarity", 0.0)) >= threshold]
        return results

    async def insert(self, table, rows):
        return rows


def test_remote_recall_sends_no_match_threshold(tmp_path):
    """Regression: recall sent match_threshold=0.7, a param the documented
    match_episodes(query_embedding, match_count) signature doesn't accept —
    PostgREST would reject the call and remote recall silently fell back."""
    fake = _FakeSupabase(rows=[])
    mem = EpisodicMemory(
        db_path=str(tmp_path / "memory.db"),
        embedder=_ControlledEmbedder(),
        supabase=fake,
    )
    asyncio.run(mem.recall("query something", k=2))
    assert fake.rpc_calls, "remote recall should have attempted the RPC"
    function, params = fake.rpc_calls[0]
    assert function == "match_episodes"
    assert params == {
        "query_embedding": [1.0, 0.0],
        "match_count": 2,
    }, f"no match_threshold may be sent, got {params}"


def test_remote_recall_keeps_negative_similarity_rows(tmp_path):
    """Regression: a 0.7 similarity floor dropped weak/negative rows, so remote
    recall returned short lists when positives were scarce — the same bug class
    as the local negative-row fix."""
    fake = _FakeSupabase(rows=[
        {"id": "a", "content": "weak match", "similarity": 0.2},
        {"id": "b", "content": "unrelated", "similarity": -0.4},
        {"id": "c", "content": "opposite", "similarity": -0.9},
    ])
    mem = EpisodicMemory(
        db_path=str(tmp_path / "memory.db"),
        embedder=_ControlledEmbedder(),
        supabase=fake,
    )
    rows = asyncio.run(mem.recall("query fill me", k=3))
    assert len(rows) == 3, "remote recall must fill top-k even with weak/negative matches"
    assert [float(r["similarity"]) for r in rows] == [0.2, -0.4, -0.9]


def test_remote_recall_falls_back_to_local_when_rpc_returns_nothing(tmp_path):
    db = str(tmp_path / "memory.db")
    seed = EpisodicMemory(db_path=db, embedder=_ControlledEmbedder(), supabase=None)
    asyncio.run(seed.remember("pos one"))
    asyncio.run(seed.close())

    fake = _FakeSupabase(rows=[])
    mem = EpisodicMemory(db_path=db, embedder=_ControlledEmbedder(), supabase=fake)
    rows = asyncio.run(mem.recall("query anything", k=2))
    assert [r["content"] for r in rows] == ["pos one"], \
        "empty remote results must fall back to local recall"
