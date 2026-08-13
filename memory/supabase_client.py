"""Supabase client — thin PostgREST wrapper used for pgvector memory sync.

Emma talks to Supabase over its REST API (httpx) — no heavy SDK required. The
pgvector search assumes a `match_episodes` RPC exists; the migration SQL is in
the README. Everything degrades gracefully: if Supabase is not configured or
unreachable, Emma keeps using local SQLite memory.

OPTIMIZATIONS:
- Circuit breaker pattern to avoid repeated failed calls
- Connection pooling with proper lifecycle
- Health check caching
"""

from __future__ import annotations

import time
from typing import Any, Optional

import httpx


class SupabaseError(RuntimeError):
    pass


class SupabaseClient:
    def __init__(
        self,
        url: Optional[str] = None,
        anon_key: Optional[str] = None,
        service_key: Optional[str] = None,
    ) -> None:
        self.url = (url or "").rstrip("/")
        self.anon_key = anon_key
        self.service_key = service_key
        self._http: Optional[httpx.AsyncClient] = None
        # Circuit breaker state
        self._circuit_breaker_open = False
        self._circuit_breaker_open_time = 0.0
        self._circuit_breaker_timeout = 60.0  # 60 seconds
        self._consecutive_failures = 0
        self._failure_threshold = 3
        # Health check cache
        self._health_cache: Optional[tuple[float, bool]] = None
        self._health_cache_ttl = 30.0  # 30 seconds
        # Schema probe cache (the HUD status endpoint polls every few seconds;
        # a positive result only needs confirming every 60s)
        self._schema_cache: Optional[tuple[float, bool]] = None
        self._schema_cache_ttl = 60.0  # 60 seconds

    # ------------------------------------------------------------------ config
    def is_configured(self) -> bool:
        return bool(self.url and (self.anon_key or self.service_key))

    def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            key = self.service_key or self.anon_key
            self._http = httpx.AsyncClient(
                base_url=self.url,
                headers={"apikey": key or "", "Authorization": f"Bearer {key or ''}"},
                timeout=10,
                limits=httpx.Limits(max_keepalive_connections=3, max_connections=6)
            )
        return self._http

    def _check_circuit_breaker(self) -> bool:
        """Check if circuit breaker is open and should reset."""
        if not self._circuit_breaker_open:
            return True
        
        # Reset circuit breaker after timeout
        if time.monotonic() - self._circuit_breaker_open_time > self._circuit_breaker_timeout:
            self._circuit_breaker_open = False
            self._consecutive_failures = 0
            return True
        
        return False

    def _record_failure(self) -> None:
        """Record a failure and potentially open circuit breaker."""
        self._consecutive_failures += 1
        if self._consecutive_failures >= self._failure_threshold:
            self._circuit_breaker_open = True
            self._circuit_breaker_open_time = time.monotonic()

    def _record_success(self) -> None:
        """Record a success and reset circuit breaker."""
        self._consecutive_failures = 0
        self._circuit_breaker_open = False

    async def close(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    # ------------------------------------------------------------------ health
    async def health(self) -> bool:
        if not self.is_configured():
            return False
        
        # Check circuit breaker
        if not self._check_circuit_breaker():
            return False
        
        # Check cache
        if self._health_cache:
            timestamp, is_healthy = self._health_cache
            if time.monotonic() - timestamp < self._health_cache_ttl:
                return is_healthy
        
        try:
            response = await self._client().get("/rest/v1/")
            is_healthy = response.status_code < 500
            self._health_cache = (time.monotonic(), is_healthy)
            if is_healthy:
                self._record_success()
            else:
                self._record_failure()
            return is_healthy
        except Exception:
            self._record_failure()
            return False

    async def schema_ok(
        self,
        table: str = "episodes",
        rpc: str = "match_episodes",
        embedding_dim: int = 384,
    ) -> Optional[bool]:
        """Verify the pgvector memory schema is actually usable end to end.

        Probes that PostgREST exposes the `episodes` table AND that the
        `match_episodes` RPC exists — the two things Emma needs to insert and
        recall memories.  Unlike health(), a missing table/RPC is a reported
        state (False), not a failure, so it never trips the circuit breaker.
        Returns None when Supabase is not configured.
        """
        if not self.is_configured():
            return None
        if not self._check_circuit_breaker():
            return False
        if self._schema_cache:
            timestamp, ok = self._schema_cache
            if time.monotonic() - timestamp < self._schema_cache_ttl:
                return ok
        try:
            table_resp = await self._client().get(
                f"/rest/v1/{table}", params={"select": "id", "limit": "1"}
            )
            if table_resp.status_code >= 400:
                if table_resp.status_code != 404:
                    self._record_failure()
                return False
            rpc_resp = await self._client().post(
                f"/rest/v1/rpc/{rpc}",
                json={"query_embedding": [0.0] * embedding_dim, "match_count": 1},
            )
            if rpc_resp.status_code >= 400:
                if rpc_resp.status_code != 404:
                    self._record_failure()
                return False
            self._record_success()
            self._schema_cache = (time.monotonic(), True)
            return True
        except Exception:
            self._record_failure()
            return False

    # ------------------------------------------------------------------ rest
    async def select(
        self,
        table: str,
        select: str = "*",
        order: Optional[str] = None,
        limit: Optional[int] = None,
        match: Optional[dict] = None,
    ) -> list[dict]:
        if not self.is_configured():
            raise SupabaseError("Supabase is not configured")
        if not self._check_circuit_breaker():
            raise SupabaseError("Circuit breaker is open")
        params: dict[str, Any] = {"select": select}
        if order:
            params["order"] = order
        if limit:
            params["limit"] = limit
        if match:
            params.update(match)
        try:
            response = await self._client().get(f"/rest/v1/{table}", params=params)
            response.raise_for_status()
            self._record_success()
            return response.json()
        except Exception as e:
            self._record_failure()
            raise SupabaseError(f"Supabase select failed: {e}")

    async def insert(self, table: str, rows: list[dict]) -> list[dict]:
        if not self.is_configured():
            raise SupabaseError("Supabase is not configured")
        if not self._check_circuit_breaker():
            raise SupabaseError("Circuit breaker is open")
        headers = {"Prefer": "return=representation"}
        try:
            response = await self._client().post(f"/rest/v1/{table}", json=rows, headers=headers)
            response.raise_for_status()
            self._record_success()
            return response.json()
        except Exception as e:
            self._record_failure()
            raise SupabaseError(f"Supabase insert failed: {e}")

    async def rpc(self, function: str, params: Optional[dict] = None) -> Any:
        if not self.is_configured():
            raise SupabaseError("Supabase is not configured")
        if not self._check_circuit_breaker():
            raise SupabaseError("Circuit breaker is open")
        try:
            response = await self._client().post(f"/rest/v1/rpc/{function}", json=params or {})
            response.raise_for_status()
            self._record_success()
            return response.json()
        except Exception as e:
            self._record_failure()
            raise SupabaseError(f"Supabase RPC failed: {e}")
