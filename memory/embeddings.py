"""Embeddings — Ollama `nomic-embed-text` with a deterministic local fallback.

If Ollama is not running (or lacks the embedding model), we fall back to a
hashed bag-of-words vector so RAG and episodic recall still function offline.

OPTIMIZATIONS:
- Connection pooling for HTTP clients
- Response caching for repeated embeddings
- Batch embedding support (concurrent embed() calls coalesced through the
  request batcher into one Ollama `/api/embed` multi-input call)
- Lazy fallback computation
- Adaptive cache with smart eviction
- Model-availability gate: when the embedding model is missing the batch
  window is skipped entirely and embeds short-circuit to the local fallback
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import re
import time
from functools import lru_cache
from typing import Optional

import httpx

from orchestration.request_batcher import RequestBatcher

_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Batch window / size for coalescing concurrent embed() calls.  Short window:
# embedding latency is on the chat hot path, and the Ollama call itself is
# cheap on localhost — the window only pays off when calls genuinely overlap.
BATCH_WINDOW_S = 0.05
BATCH_MAX_SIZE = 8

# How long to trust a negative /api/tags probe before re-checking.
MODEL_AVAIL_CACHE_S = 30.0

# Import adaptive cache for smart caching
try:
    from memory.adaptive_cache import AdaptiveCache, EvictionPolicy
    USE_ADAPTIVE_CACHE = True
except ImportError:
    USE_ADAPTIVE_CACHE = False


class Embedder:
    def __init__(
        self,
        ollama_url: str = "http://localhost:11434",
        model: str = "nomic-embed-text",
        dim: int = 384,
        batch_window: float = BATCH_WINDOW_S,
        batch_size: int = BATCH_MAX_SIZE,
    ) -> None:
        self.ollama_url = ollama_url.rstrip("/")
        self.model = model
        self.dim = dim
        # Use connection pooling for better performance
        self._client: Optional[httpx.AsyncClient] = None
        self._cache_enabled = True

        # Coalesce concurrent embed() calls into batched Ollama calls.
        self._batcher = RequestBatcher(
            batch_processor=self._embed_batch_processor,
            max_batch_size=batch_size,
            max_wait_time=batch_window,
        )
        # Cached model-availability probe so a missing embedding model doesn't
        # make every embed() pay a network round trip + batch window.
        self._avail_cache: Optional[tuple[float, bool]] = None

        # Use adaptive cache if available
        if USE_ADAPTIVE_CACHE:
            self._adaptive_cache = AdaptiveCache(
                max_size=2000,
                default_ttl=3600.0,  # 1 hour
                eviction_policy=EvictionPolicy.LRU,
                adaptive_ttl=True
            )
        else:
            self._adaptive_cache = None
            # Fallback to simple dict cache
            self._embedding_cache = {}
        
    def _get_client(self) -> httpx.AsyncClient:
        """Get or create a pooled HTTP client."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=30.0,
                limits=httpx.Limits(max_keepalive_connections=5, max_connections=10)
            )
        return self._client

    # ------------------------------------------------------------------ main
    async def embed(self, text: str) -> list[float]:
        # Check cache first for common texts
        if self._cache_enabled:
            cached = self._get_cached_embedding(text)
            if cached is not None:
                return cached

        # If Ollama has no embedding model, every network call would fail —
        # take the deterministic fallback directly (no network, no batch
        # window).  The probe is cached for MODEL_AVAIL_CACHE_S.
        if not await self._is_model_available():
            return self._fallback(text)

        try:
            vector = await self._batcher.submit(text)
        except Exception:
            vector = None
        if vector:
            if self._cache_enabled:
                self._cache_embedding(text, vector)
            return vector
        return self._fallback(text)

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed multiple texts, submitting them concurrently so the batcher
        coalesces them into a single Ollama call."""
        if not texts:
            return []
        return list(await asyncio.gather(*(self.embed(t) for t in texts)))

    # ------------------------------------------------------------------ batching
    async def _embed_batch_processor(self, texts: list[str]) -> list[list[float]]:
        """Batch embed via Ollama's multi-input `/api/embed`, with fallbacks.

        Never raises: any network/model failure degrades to the deterministic
        local fallback vector, matching single-embed semantics.
        """
        try:
            client = self._get_client()
            response = await client.post(
                f"{self.ollama_url}/api/embed",
                json={"model": self.model, "input": [t[:8000] for t in texts]},
            )
            response.raise_for_status()
            embeddings = response.json().get("embeddings")
            if embeddings and len(embeddings) == len(texts):
                return embeddings
        except Exception:
            pass
        # Fallback: legacy per-text endpoint, then deterministic local vectors.
        return [await self._embed_single(t) for t in texts]

    async def _embed_single(self, text: str) -> list[float]:
        try:
            client = self._get_client()
            response = await client.post(
                f"{self.ollama_url}/api/embeddings",
                json={"model": self.model, "prompt": text[:8000]},
            )
            response.raise_for_status()
            vector = response.json().get("embedding")
            if vector:
                return vector
        except Exception:
            pass
        return self._fallback(text)

    async def _is_model_available(self) -> bool:
        """Whether Ollama currently has the embedding model (30s cached probe)."""
        now = time.monotonic()
        if self._avail_cache is not None and now - self._avail_cache[0] < MODEL_AVAIL_CACHE_S:
            return self._avail_cache[1]
        try:
            client = self._get_client()
            response = await client.get(f"{self.ollama_url}/api/tags")
            names = [m.get("name", "") for m in response.json().get("models", [])]
            ok = any(
                name == self.model
                or name.startswith(self.model + ":")
                or name.startswith(self.model + "-")
                for name in names
            )
        except Exception:
            ok = False
        self._avail_cache = (now, ok)
        return ok

    async def close(self) -> None:
        """Clean up resources."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        if self._batcher is not None:
            await self._batcher.close()
    
    # ------------------------------------------------------------------ caching
    def _get_cached_embedding(self, text: str) -> Optional[list[float]]:
        """Get cached embedding using adaptive cache if available."""
        if USE_ADAPTIVE_CACHE and self._adaptive_cache:
            return self._adaptive_cache.get(text)
        
        # Fallback to simple cache
        if not hasattr(self, '_embedding_cache'):
            self._embedding_cache = {}
        text_hash = hashlib.md5(text.encode()).hexdigest()
        return self._embedding_cache.get(text_hash)
    
    def _cache_embedding(self, text: str, vector: list[float]) -> None:
        """Cache an embedding with adaptive cache if available."""
        if USE_ADAPTIVE_CACHE and self._adaptive_cache:
            self._adaptive_cache.set(text, vector)
            return
        
        # Fallback to simple cache
        if not hasattr(self, '_embedding_cache'):
            self._embedding_cache = {}
        
        # Limit cache size to prevent memory bloat
        if len(self._embedding_cache) > 1000:
            # Remove oldest 20% of entries
            keys_to_remove = list(self._embedding_cache.keys())[:200]
            for key in keys_to_remove:
                del self._embedding_cache[key]
        
        text_hash = hashlib.md5(text.encode()).hexdigest()
        self._embedding_cache[text_hash] = vector
    
    def get_cache_stats(self) -> dict:
        """Get cache statistics."""
        if USE_ADAPTIVE_CACHE and self._adaptive_cache:
            return self._adaptive_cache.stats()
        
        if hasattr(self, '_embedding_cache'):
            return {
                "size": len(self._embedding_cache),
                "type": "simple_dict"
            }
        return {"size": 0, "type": "none"}

    # ------------------------------------------------------------------ local
    def _fallback(self, text: str) -> list[float]:
        """Deterministic hashed bag-of-words embedding (L2-normalized)."""
        vector = [0.0] * self.dim
        for token in _TOKEN_RE.findall(text.lower()):
            digest = hashlib.blake2b(token.encode(), digest_size=8).digest()
            index = int.from_bytes(digest[:4], "big") % self.dim
            sign = 1.0 if digest[4] & 1 else -1.0
            vector[index] += sign
        norm = math.sqrt(sum(v * v for v in vector)) or 1.0
        return [v / norm for v in vector]

    # ------------------------------------------------------------------ math
    @staticmethod
    def cosine(a: list[float], b: list[float]) -> float:
        if not a or not b:
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a)) or 1.0
        nb = math.sqrt(sum(y * y for y in b)) or 1.0
        return dot / (na * nb)
