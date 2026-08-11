"""RAG pipeline — retrieve relevant memories and build prompt context.

Retrieval merges:
1. Local episodic recall (SQLite + cosine similarity).
2. Remote pgvector search via Supabase `match_episodes` RPC (if configured).

The result is formatted as a compact context block the reasoning agent injects
into the LLM prompt.

OPTIMIZATIONS:
- Performance monitoring with latency tracking
- Batch retrieval support
"""

from __future__ import annotations

from typing import Optional

from memory.embeddings import Embedder
from memory.episodic import EpisodicMemory
from memory.supabase_client import SupabaseClient

# Import performance monitoring
try:
    from performance.monitor import track_latency, perf_monitor
    USE_PERF_MONITOR = True
except ImportError:
    USE_PERF_MONITOR = False
    from performance.monitor import no_op_latency as track_latency


class RAGPipeline:
    def __init__(
        self,
        episodic: EpisodicMemory,
        embedder: Embedder,
        supabase: Optional[SupabaseClient] = None,
        top_k: int = 5,
    ) -> None:
        self.episodic = episodic
        self.embedder = embedder
        self.supabase = supabase
        self.top_k = top_k

    @track_latency("rag_retrieve")
    async def retrieve(self, query: str, k: int = 5) -> list[dict]:
        local = await self.episodic.recall(query, k=k)
        remote: list[dict] = []
        if self.supabase is not None and self.supabase.is_configured():
            try:
                vector = await self.embedder.embed(query)
                result = await self.supabase.rpc(
                    "match_episodes",
                    {"query_embedding": vector, "match_count": k},
                )
                remote = [
                    {
                        "id": row.get("id"),
                        "ts": row.get("created_at") or row.get("ts"),
                        "kind": row.get("kind", "episode"),
                        "content": row.get("content", ""),
                        "score": float(row.get("similarity", 0.0)),
                    }
                    for row in (result or [])
                ]
            except Exception:
                remote = []
        return (local + remote)[:k]

    @track_latency("rag_augment")
    async def augment(self, query: str, k: Optional[int] = None) -> str:
        """Return a formatted context block, or '' when nothing is relevant."""
        items = await self.retrieve(query, k=k or self.top_k)
        if not items:
            return ""
        blocks = []
        for index, item in enumerate(items, start=1):
            content = str(item.get("content", ""))[:500].replace("\n", " ")
            score = item.get("score", 0.0)
            ts = item.get("ts", "")
            blocks.append(f"[{index}] (score {score:.3f}, {ts}) {content}")
        return "Relevant context:\n" + "\n".join(blocks)
