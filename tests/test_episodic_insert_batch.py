"""EpisodicMemory remember() batching — concurrent remembers must coalesce into
ONE Supabase insert via the RequestBatcher, with per-caller SQLite fallback
when the batched insert fails or shutdown interrupts it."""

import asyncio
import hashlib
import json

from memory.episodic import EpisodicMemory
from memory.supabase_client import SupabaseError


class _FakeEmbedder:
    async def embed(self, text):
        digest = hashlib.blake2b(text.encode(), digest_size=8).digest()
        return [digest[i] / 255.0 for i in range(4)]


class _FakeSupabase:
    def __init__(self, fail=False):
        self.fail = fail
        self.insert_calls = []

    def is_configured(self):
        return True

    async def insert(self, table, rows):
        if self.fail:
            raise SupabaseError("insert failed")
        self.insert_calls.append((table, rows))
        return rows

    async def rpc(self, function, params):
        return []


def _memory(tmp_path, supabase):
    return EpisodicMemory(
        db_path=str(tmp_path / "memory.db"),
        embedder=_FakeEmbedder(),
        supabase=supabase,
    )


def test_concurrent_remembers_coalesce_into_one_insert(tmp_path):
    fake = _FakeSupabase()
    mem = _memory(tmp_path, fake)

    async def run():
        try:
            return await asyncio.gather(*(
                mem.remember(f"episode {i}", payload={"n": i}) for i in range(5)
            ))
        finally:
            await mem.close()

    ids = asyncio.run(run())

    assert len(fake.insert_calls) == 1, \
        f"expected ONE insert call, got {len(fake.insert_calls)}"
    table, rows = fake.insert_calls[0]
    assert table == "episodes"
    assert len(rows) == 5
    assert len(ids) == 5 and len(set(ids)) == 5, "each remember returns its own id"
    assert {r["id"] for r in rows} == set(ids)
    assert all(r["kind"] == "episode" for r in rows)
    assert {json.loads(r["payload"])["n"] for r in rows} == {0, 1, 2, 3, 4}
    assert all(len(json.loads(r["embedding"])) == 4 for r in rows)
    assert mem._all_rows() == [], "nothing may fall back to SQLite when Supabase works"


def test_each_caller_gets_its_own_id_and_row(tmp_path):
    fake = _FakeSupabase()
    mem = _memory(tmp_path, fake)

    async def run():
        try:
            return await asyncio.gather(*(
                mem.remember(f"topic {i}") for i in range(3)
            ))
        finally:
            await mem.close()

    results = asyncio.run(run())
    rows = fake.insert_calls[0][1]
    by_id = {r["id"]: r for r in rows}
    for rid, i in zip(results, range(3)):
        assert by_id[rid]["content"] == f"topic {i}", \
            "the id returned to a caller must match ITS row"


def test_supabase_failure_falls_back_to_sqlite(tmp_path):
    fake = _FakeSupabase(fail=True)
    mem = _memory(tmp_path, fake)

    async def run():
        try:
            return await asyncio.gather(*(
                mem.remember(f"fail {i}") for i in range(4)
            ))
        finally:
            await mem.close()

    ids = asyncio.run(run())
    assert len(ids) == 4
    rows = mem._all_rows()
    assert len(rows) == 4, "every failed insert must fall back to SQLite"
    assert {r["content"] for r in rows} == {f"fail {i}" for i in range(4)}
    assert {r["id"] for r in rows} == set(ids)


def test_supabase_unconfigured_writes_sqlite_directly(tmp_path):
    mem = _memory(tmp_path, None)
    assert mem._insert_batcher is None, "no batcher when Supabase is absent"

    async def run():
        try:
            return await mem.remember("local only", payload={"x": 1})
        finally:
            await mem.close()

    rid = asyncio.run(run())
    rows = mem._all_rows()
    assert len(rows) == 1
    assert rows[0]["id"] == rid
    assert json.loads(rows[0]["payload"]) == {"x": 1}


def test_close_during_inflight_batch_falls_back_to_sqlite(tmp_path):
    """Shutdown must not strand concurrent remembers: in-flight batch futures
    are rejected by close() and every caller falls back to SQLite."""
    gate = asyncio.Event()

    class _SlowSupabase(_FakeSupabase):
        async def insert(self, table, rows):
            await gate.wait()  # hold the batch open until close() interrupts
            return rows

    mem = _memory(tmp_path, _SlowSupabase())

    async def run():
        tasks = [asyncio.ensure_future(mem.remember(f"s{i}")) for i in range(3)]
        await asyncio.sleep(0.12)  # the batch is now in-flight inside insert()
        await mem.close()          # rejects the in-flight futures
        gate.set()
        return await asyncio.wait_for(asyncio.gather(*tasks), timeout=3)

    results = asyncio.run(run())
    assert len(results) == 3
    rows = mem._all_rows()
    assert len(rows) == 3, "closed in-flight remembers must fall back to SQLite"
    assert {r["content"] for r in rows} == {f"s{i}" for i in range(3)}
