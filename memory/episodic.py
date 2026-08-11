"""Episodic memory — Supabase-primary with SQLite fallback store of interaction episodes.

Every user turn is stored as an episode (kind: "user"), and significant agent
outcomes are stored as well. Recall ranks episodes by embedding cosine
similarity against the query. Supabase is the primary storage with SQLite as
fallback when Supabase is unavailable.

OPTIMIZATIONS:
- SQLite connection pooling
- Efficient similarity scoring with early termination
- Batch embedding support
- Caching of recent queries
- Performance monitoring
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from functools import lru_cache
import threading

from memory.embeddings import Embedder
from memory.supabase_client import SupabaseClient, SupabaseError

# Import performance monitoring
try:
    from performance.monitor import track_latency, perf_monitor
    USE_PERF_MONITOR = True
except ImportError:
    USE_PERF_MONITOR = False
    from performance.monitor import no_op_latency as track_latency

_SCHEMA = """
CREATE TABLE IF NOT EXISTS episodes (
    id         TEXT PRIMARY KEY,
    ts         TEXT NOT NULL,
    kind       TEXT NOT NULL DEFAULT 'episode',
    content    TEXT NOT NULL,
    payload    TEXT,
    embedding  TEXT
);
CREATE INDEX IF NOT EXISTS idx_episodes_ts ON episodes(ts);
CREATE INDEX IF NOT EXISTS idx_episodes_kind ON episodes(kind);
"""


class EpisodicMemory:
    def __init__(
        self,
        db_path: str | Path = "data/memory.db",
        embedder: Optional[Embedder] = None,
        supabase: Optional[SupabaseClient] = None
    ) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.embedder = embedder
        self.supabase = supabase
        # Thread-local connection for SQLite connection pooling
        self._local = threading.local()
        # Cache for recent query results
        self._query_cache = {}
        self._cache_ttl = 60  # seconds
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    # ------------------------------------------------------------------ db
    def _connect(self) -> sqlite3.Connection:
        """Get or create a thread-local SQLite connection with connection pooling."""
        if not hasattr(self._local, 'connection') or self._local.connection is None:
            self._local.connection = sqlite3.connect(
                self.db_path,
                check_same_thread=False,  # Allow connection sharing
                timeout=30.0
            )
            self._local.connection.row_factory = sqlite3.Row
            # Enable WAL mode for better concurrency
            self._local.connection.execute("PRAGMA journal_mode=WAL")
            self._local.connection.execute("PRAGMA synchronous=NORMAL")
            self._local.connection.execute("PRAGMA cache_size=-64000")  # 64MB cache
        return self._local.connection

    @track_latency("episodic_remember")
    async def remember(self, content: str, kind: str = "episode", payload: Optional[dict] = None) -> str:
        episode_id = uuid.uuid4().hex[:12]
        ts = datetime.now(timezone.utc).isoformat()
        embedding = None
        if self.embedder is not None:
            embedding = await self.embedder.embed(content)
        
        # Try Supabase first (primary storage)
        if self.supabase and self.supabase.is_configured():
            try:
                row = {
                    "id": episode_id,
                    "ts": ts,
                    "kind": kind,
                    "content": content,
                    "payload": json.dumps(payload) if payload is not None else None,
                    "embedding": json.dumps(embedding) if embedding else None
                }
                await self.supabase.insert("episodes", [row])
                return episode_id
            except SupabaseError as e:
                # Fallback to SQLite if Supabase fails
                print(f"Supabase storage failed, falling back to SQLite: {e}")
        
        # SQLite fallback
        embedding_json = json.dumps(embedding) if embedding else None
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO episodes (id, ts, kind, content, payload, embedding) VALUES (?, ?, ?, ?, ?, ?)",
                (episode_id, ts, kind, content, json.dumps(payload) if payload is not None else None, embedding_json),
            )
        return episode_id

    async def remember_batch(self, episodes: list[dict]) -> list[str]:
        """Remember multiple episodes with batch embedding for efficiency.
        
        Args:
            episodes: List of dicts with keys: content, kind (optional), payload (optional)
        
        Returns:
            List of episode IDs
        """
        if not episodes:
            return []
        
        # Batch generate embeddings
        embeddings = []
        if self.embedder is not None:
            texts = [ep["content"] for ep in episodes]
            embeddings = await self.embedder.embed_batch(texts)
        
        episode_ids = []
        now = datetime.now(timezone.utc).isoformat()
        
        # Try Supabase batch insert first
        if self.supabase and self.supabase.is_configured():
            try:
                rows = []
                for i, ep in enumerate(episodes):
                    episode_id = uuid.uuid4().hex[:12]
                    episode_ids.append(episode_id)
                    embedding = embeddings[i] if embeddings else None
                    rows.append({
                        "id": episode_id,
                        "ts": now,
                        "kind": ep.get("kind", "episode"),
                        "content": ep["content"],
                        "payload": json.dumps(ep.get("payload")) if ep.get("payload") else None,
                        "embedding": json.dumps(embedding) if embedding else None
                    })
                await self.supabase.insert("episodes", rows)
                return episode_ids
            except SupabaseError as e:
                print(f"Supabase batch insert failed, falling back to SQLite: {e}")
        
        # SQLite fallback with batch insert
        with self._connect() as conn:
            for i, ep in enumerate(episodes):
                episode_id = uuid.uuid4().hex[:12]
                episode_ids.append(episode_id)
                embedding = embeddings[i] if embeddings else None
                conn.execute(
                    "INSERT INTO episodes (id, ts, kind, content, payload, embedding) VALUES (?, ?, ?, ?, ?, ?)",
                    (episode_id, now, ep.get("kind", "episode"), ep["content"], 
                     json.dumps(ep.get("payload")) if ep.get("payload") else None,
                     json.dumps(embedding) if embedding else None),
                )
        return episode_ids

    # ------------------------------------------------------------------ read
    def _prune_query_cache(self) -> None:
        """Drop expired query-cache entries so the cache stays bounded.

        The 60s TTL is enforced at read time, but entries were never removed,
        so the dict grew without bound over a session.  Prune whenever it
        exceeds a modest threshold.
        """
        if len(self._query_cache) <= 200:
            return
        now = datetime.now().timestamp()
        expired = [
            key for key, entry in self._query_cache.items()
            if now - entry['timestamp'] >= self._cache_ttl
        ]
        for key in expired:
            del self._query_cache[key]
        # If everything was still fresh, keep the cache from growing forever
        # by dropping the oldest half.
        if len(self._query_cache) > 200:
            for key in sorted(self._query_cache, key=lambda k: self._query_cache[k]['timestamp'])[:len(self._query_cache) // 2]:
                del self._query_cache[key]

    @track_latency("episodic_recall")
    async def recall(self, query: str, k: int = 5) -> list[dict]:
        # Check query cache first
        cache_key = f"{query}:{k}"
        cache_entry = self._query_cache.get(cache_key)
        if cache_entry and (datetime.now().timestamp() - cache_entry['timestamp']) < self._cache_ttl:
            return cache_entry['results']
        
        # Try Supabase first (primary storage)
        if self.supabase and self.supabase.is_configured() and self.embedder is not None:
            try:
                query_vec = await self.embedder.embed(query)
                results = await self.supabase.rpc("match_episodes", {
                    "query_embedding": query_vec,
                    "match_threshold": 0.7,
                    "match_count": k
                })
                if results:
                    # Cache the results
                    self._prune_query_cache()
                    self._query_cache[cache_key] = {
                        'timestamp': datetime.now().timestamp(),
                        'results': results[:k]
                    }
                    return results[:k]
            except SupabaseError as e:
                # Fallback to SQLite if Supabase fails
                print(f"Supabase recall failed, falling back to SQLite: {e}")
        
        # SQLite fallback with optimized similarity scoring
        rows = self._all_rows()
        if not rows:
            return []
        if self.embedder is not None:
            query_vec = await self.embedder.embed(query)
            scored: list[tuple[dict, float]] = []
            # Early termination optimization.  The threshold only becomes
            # meaningful once k+5 candidates have been collected; starting at
            # 0.0 would drop negative-cosine rows even when fewer than k
            # positive matches exist, so it starts at -inf.
            min_score = float("-inf")
            for row in rows:
                vec = self._decode_embedding(row.get("embedding"))
                if vec is None:
                    vec = self._fallback_embedding(row.get("content", ""))
                score = Embedder.cosine(query_vec, vec)
                if score >= min_score:
                    scored.append((row, score))
                    # Maintain only top k+5 to reduce memory
                    if len(scored) > k + 5:
                        scored.sort(key=lambda pair: pair[1], reverse=True)
                        scored = scored[:k + 5]
                        min_score = scored[-1][1] if scored else float("-inf")
            scored.sort(key=lambda pair: pair[1], reverse=True)
            results = [self._decorate(row, score) for row, score in scored[:k]]
            # Cache the results
            self._prune_query_cache()
            self._query_cache[cache_key] = {
                'timestamp': datetime.now().timestamp(),
                'results': results
            }
            return results
        return rows[:k]

    def recent(self, limit: int = 20) -> list[dict]:
        rows = self._all_rows(order_desc=True)
        return rows[:limit]

    def count(self) -> int:
        with self._connect() as conn:
            return conn.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]

    def clear(self) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM episodes")
        # Clear cache on clear
        self._query_cache.clear()

    async def close(self) -> None:
        """Clean up database connections."""
        if hasattr(self._local, 'connection') and self._local.connection:
            self._local.connection.close()
            self._local.connection = None

    # ------------------------------------------------------------------ internals
    def _all_rows(self, order_desc: bool = False) -> list[dict]:
        order = "ORDER BY ts DESC" if order_desc else ""
        with self._connect() as conn:
            rows = conn.execute(f"SELECT id, ts, kind, content, payload, embedding FROM episodes {order}").fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _decode_embedding(raw: Optional[str]) -> Optional[list[float]]:
        if not raw:
            return None
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return None

    def _fallback_embedding(self, content: str) -> list[float]:
        if self.embedder is None:
            return []
        return self.embedder._fallback(content)

    @staticmethod
    def _decorate(row: dict, score: float) -> dict:
        payload = None
        if row.get("payload"):
            try:
                payload = json.loads(row["payload"])
            except (json.JSONDecodeError, TypeError):
                payload = row["payload"]
        return {
            "id": row.get("id"),
            "ts": row.get("ts"),
            "kind": row.get("kind"),
            "content": row.get("content"),
            "payload": payload,
            "score": round(score, 4),
        }
