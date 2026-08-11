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
