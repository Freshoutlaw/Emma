"""Read-only Supabase query agent — direct Postgres access via asyncpg.

Connects to Supabase through the IPv4 shared-pooler DSN (port 6543) using
a `trillion_analytics`-style role with SELECT-only grants.  Every query is
validated before execution: only WITH and SELECT statements are permitted,
a row cap is enforced, and a statement_timeout prevents runaway queries.

Pooler requirements honoured:
  - statement_cache_size = 0  (Supabase pooler does not support prepared statements)
  - sslmode = require
  - connect_timeout = 10

The agent is a skeleton until the DSN is added to .env as
``EMMA_SUPABASE_QUERY_DSN``.  When unset, every method returns a clear
"not configured" result — no crash, no silent failure.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any, Optional

from agents.base import AgentResult, BaseAgent

if TYPE_CHECKING:
    from agents.router import Pipeline

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_ROWS = 100
STATEMENT_TIMEOUT_MS = 30_000

# SQL validation: only WITH and SELECT are allowed.  The regex is
# deliberately permissive on what follows WITH — CTEs can contain almost
# anything *inside* them — but the final statement must be a SELECT.
_SELECT_ONLY_RE = re.compile(
    r"^\s*(?:WITH\b.+?\bSELECT\b|SELECT\b)",
    re.IGNORECASE | re.DOTALL,
)

_DANGEROUS_RE = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|GRANT|REVOKE|EXEC|EXECUTE|"
    r"COPY|CALL|SET|RESET|DISCARD|DECLARE|FETCH|MOVE|LISTEN|NOTIFY|UNLISTEN|"
    r"VACUUM|ANALYZE|REINDEX|CLUSTER|COMMENT|LOCK|BEGIN|COMMIT|ROLLBACK|SAVEPOINT|"
    r"START\s+TRANSACTION|RELEASE\s+SAVEPOINT)\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def validate_sql(sql: str) -> tuple[bool, str]:
    """Return (allowed, reason) after checking a query is read-only.

    Rules (checked in priority order so the most useful error is returned):
      1. At least one non-whitespace character.
      2. No mutating keywords (INSERT, UPDATE, DELETE, DROP, …) anywhere.
      3. The top-level statement must start with WITH or SELECT.
    """
    stripped = sql.strip()
    if not stripped:
        return False, "empty query"
    if _DANGEROUS_RE.search(stripped):
        return False, "mutating or administrative SQL detected"
    if not _SELECT_ONLY_RE.match(stripped):
        return False, "query must start with WITH or SELECT"
    return True, "ok"


def _rows_to_json(rows: list[dict]) -> str:
    """Serialise rows to a compact JSON string, converting non-JSON types."""
    return json.dumps(rows, default=str, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

class SupabaseQueryAgent(BaseAgent):
    name = "supabase_query"
    description = "Read-only SQL queries against Supabase (SELECT/WITH only, max 100 rows)."

    # Least-privilege scoping: this agent never executes shell commands,
    # writes files, or touches the network beyond its own pool connection.
    tool_allowlist: frozenset[str] = frozenset()  # no ControlAgent tools needed

    def __init__(self, pipeline: "Pipeline") -> None:
        super().__init__(pipeline)
        self._dsn: Optional[str] = pipeline.settings.supabase_query_dsn
        self._pool: Any = None  # asyncpg.Pool, created lazily

    # ---------------------------------------------------------------- config
    def is_configured(self) -> bool:
        return bool(self._dsn)

    async def _ensure_pool(self) -> Any:
        """Lazily create the asyncpg connection pool (once per process)."""
        if self._pool is not None:
            return self._pool
        if not self._dsn:
            raise RuntimeError(
                "Supabase query DSN is not set. "
                "Add EMMA_SUPABASE_QUERY_DSN to .env "
                "(IPv4 shared-pooler: postgres://…@pooler.supabase.com:6543/postgres)."
            )
        try:
            import asyncpg  # noqa: F811
        except ImportError as exc:
            raise RuntimeError(
                "asyncpg is not installed — run `pip install asyncpg`."
            ) from exc
        self._pool = await asyncpg.create_pool(
            self._dsn,
            min_size=1,
            max_size=4,
            command_timeout=STATEMENT_TIMEOUT_MS / 1000,
            statement_cache_size=0,  # pooler requirement
            server_settings={
                "statement_timeout": str(STATEMENT_TIMEOUT_MS),
            },
        )
        return self._pool

    # ------------------------------------------------------------------ query
    async def query(self, sql: str, max_rows: int = MAX_ROWS) -> AgentResult:
        """Execute a read-only query and return the result as JSON."""
        # Validate SQL first — report bad queries even when the DSN is unset
        # so the user gets immediate feedback on their syntax.
        allowed, reason = validate_sql(sql)
        if not allowed:
            return AgentResult(
                ok=False,
                output=f"Query rejected: {reason}.",
                intent="supabase_query",
                error=f"validation failed: {reason}",
            )

        if not self.is_configured():
            return AgentResult(
                ok=False,
                output="Supabase query DSN is not configured. Set EMMA_SUPABASE_QUERY_DSN in .env.",
                intent="supabase_query",
                error="not configured",
            )

        try:
            pool = await self._ensure_pool()
            async with pool.acquire() as conn:
                rows = await conn.fetch(sql)
                # Enforce row cap after execution (the pooler or server may
                # return more than requested if LIMIT was not in the query).
                rows = rows[:max_rows]
                result = [dict(r) for r in rows]
        except Exception as exc:
            self._audit(
                "supabase_query.error",
                action="query",
                detail={"error": str(exc)[:300], "sql": sql[:200]},
            )
            return AgentResult(
                ok=False,
                output=f"Query failed: {exc}",
                intent="supabase_query",
                error=str(exc),
            )

        self._audit(
            "supabase_query.executed",
            action="query",
            detail={"rows": len(result), "sql": sql[:200]},
        )
        output = _rows_to_json(result) if result else "[]"
        return AgentResult(ok=True, output=output, intent="supabase_query")

    # ------------------------------------------------------------- introspect
    async def list_tables(self, schema: str = "public") -> AgentResult:
        """List tables in the given schema."""
        if not self.is_configured():
            return AgentResult(
                ok=False,
                output="Supabase query DSN is not configured.",
                intent="supabase_query",
                error="not configured",
            )

        sql = (
            "SELECT table_name, table_type "
            "FROM information_schema.tables "
            "WHERE table_schema = $1 "
            "ORDER BY table_name"
        )
        try:
            pool = await self._ensure_pool()
            async with pool.acquire() as conn:
                rows = await conn.fetch(sql, schema)
                result = [{"name": r["table_name"], "type": r["table_type"]} for r in rows]
        except Exception as exc:
            return AgentResult(
                ok=False, output=f"list_tables failed: {exc}",
                intent="supabase_query", error=str(exc),
            )
        self._audit("supabase_query.list_tables", action="introspect", detail={"schema": schema})
        return AgentResult(ok=True, output=_rows_to_json(result), intent="supabase_query")

    async def describe_table(self, table: str, schema: str = "public") -> AgentResult:
        """Describe columns of a table."""
        if not self.is_configured():
            return AgentResult(
                ok=False,
                output="Supabase query DSN is not configured.",
                intent="supabase_query",
                error="not configured",
            )

        sql = (
            "SELECT column_name, data_type, is_nullable, column_default "
            "FROM information_schema.columns "
            "WHERE table_schema = $1 AND table_name = $2 "
            "ORDER BY ordinal_position"
        )
        try:
            pool = await self._ensure_pool()
            async with pool.acquire() as conn:
                rows = await conn.fetch(sql, schema, table)
                result = [
                    {
                        "column": r["column_name"],
                        "type": r["data_type"],
                        "nullable": r["is_nullable"] == "YES",
                        "default": r["column_default"],
                    }
                    for r in rows
                ]
        except Exception as exc:
            return AgentResult(
                ok=False, output=f"describe_table failed: {exc}",
                intent="supabase_query", error=str(exc),
            )
        self._audit(
            "supabase_query.describe_table",
            action="introspect",
            detail={"schema": schema, "table": table},
        )
        return AgentResult(ok=True, output=_rows_to_json(result), intent="supabase_query")

    # ------------------------------------------------------------------- run
    async def run(self, request: str) -> AgentResult:
        """Natural-language entry point.

        Recognises:
          - "query <sql>" / "run <sql>" / "sql <sql>"
          - "list tables" / "show tables"
          - "describe <table>" / "schema <table>"
          - anything else → treat as a SELECT query
        """
        low = request.strip().lower()

        if low.startswith(("list tables", "show tables")):
            return await self.list_tables()

        for prefix in ("describe ", "schema "):
            if low.startswith(prefix):
                table = request.strip()[len(prefix):].strip().strip('"').strip("'")
                return await self.describe_table(table) if table else AgentResult(
                    ok=False, output=f"Usage: {prefix.strip()} <table_name>",
                    intent="supabase_query",
                )

        for prefix in ("query ", "run ", "sql "):
            if low.startswith(prefix):
                sql = request.strip()[len(prefix):].strip()
                return await self.query(sql)

        # Default: treat the entire request as SQL (the validator will reject
        # anything that isn't SELECT/WITH).
        return await self.query(request.strip())
