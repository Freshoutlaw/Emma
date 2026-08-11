"""LLM usage capture — one row per LLM call, best-effort, never breaks a turn.

The recorder is wired into the LLM client via a module-level setter
(`set_usage_repo`) called once at startup, so the client doesn't need a repo
threaded through every call. `record_usage` is wrapped in a catch-all: a
metrics feature that can take down a conversation turn is worse than no
feature, so recording cost can never raise into the conversation path.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from cost.pricing import cache_savings, compute_cost

log = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS usage (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    ts                 TEXT NOT NULL,
    model              TEXT NOT NULL,
    input_tokens       INTEGER NOT NULL DEFAULT 0,
    output_tokens      INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens  INTEGER NOT NULL DEFAULT 0,
    cache_write_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd           REAL NOT NULL DEFAULT 0,
    source             TEXT NOT NULL DEFAULT 'conversation'
);
CREATE INDEX IF NOT EXISTS idx_usage_ts ON usage(ts);
CREATE INDEX IF NOT EXISTS idx_usage_model ON usage(model);
"""


class UsageRepo:
    """SQLite store of per-call LLM usage. One row per call — a heavy month is
    a few thousand rows; no rollups needed."""

    def __init__(self, db_path: str | Path = "data/usage.db") -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    # ---------------------------------------------------------------- write
    def record(
        self,
        model: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
        cost_usd: float = 0.0,
        source: str = "conversation",
        ts: Optional[str] = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO usage (ts, model, input_tokens, output_tokens, cache_read_tokens,"
                " cache_write_tokens, cost_usd, source) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    ts or datetime.now(timezone.utc).isoformat(),
                    model,
                    int(input_tokens),
                    int(output_tokens),
                    int(cache_read_tokens),
                    int(cache_write_tokens),
                    float(cost_usd),
                    source,
                ),
            )

    # ---------------------------------------------------------------- read
    def usage_since(self, start: str, end: str) -> dict[str, Any]:
        """Aggregate rows with start <= ts < end.

        Returns totals, a per-model breakdown, and a per-day breakdown —
        every dashboard query is a time-range scan over the ts index.
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT model, ts, source, input_tokens, output_tokens, cache_read_tokens,"
                " cache_write_tokens, cost_usd FROM usage WHERE ts >= ? AND ts < ?",
                (start, end),
            ).fetchall()

        total_input = total_output = total_cached = total_cost = 0
        per_model: dict[str, dict[str, float]] = {}
        per_day: dict[str, dict[str, float]] = {}
        per_source: dict[str, dict[str, float]] = {}
        savings = 0.0
        for row in rows:
            model = row["model"]
            source = row["source"] or "unknown"
            total_input += row["input_tokens"]
            total_output += row["output_tokens"]
            total_cached += row["cache_read_tokens"]
            total_cost += row["cost_usd"]
            savings += cache_savings(model, row["cache_read_tokens"])

            m = per_model.setdefault(model, {"calls": 0, "cost_usd": 0.0, "input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0})
            m["calls"] += 1
            m["cost_usd"] += row["cost_usd"]
            m["input_tokens"] += row["input_tokens"]
            m["output_tokens"] += row["output_tokens"]
            m["cache_read_tokens"] += row["cache_read_tokens"]

            day = row["ts"][:10]
            d = per_day.setdefault(day, {"calls": 0, "cost_usd": 0.0})
            d["calls"] += 1
            d["cost_usd"] += row["cost_usd"]

            src = per_source.setdefault(source, {"calls": 0, "cost_usd": 0.0, "input_tokens": 0, "output_tokens": 0})
            src["calls"] += 1
            src["cost_usd"] += row["cost_usd"]
            src["input_tokens"] += row["input_tokens"]
            src["output_tokens"] += row["output_tokens"]

        return {
            "calls": len(rows),
            "input_tokens": total_input,
            "output_tokens": total_output,
            "cache_read_tokens": total_cached,
            "cost_usd": total_cost,
            "cache_savings_usd": savings,
            "per_model": [
                {"model": model, **stats}
                for model, stats in sorted(per_model.items(), key=lambda kv: kv[1]["cost_usd"], reverse=True)
            ],
            "per_day": sorted(
                [{"date": day, **stats} for day, stats in per_day.items()],
                key=lambda d: d["date"],
                reverse=True,  # newest day first
            ),
            "per_source": [
                {"source": source, **stats}
                for source, stats in sorted(per_source.items(), key=lambda kv: kv[1]["cost_usd"], reverse=True)
            ],
        }

    def recent(self, limit: int = 12) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT ts, model, input_tokens, output_tokens, cache_read_tokens,"
                " cache_write_tokens, cost_usd, source FROM usage ORDER BY ts DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]


# ================================================================== recorder
_repo: Optional[UsageRepo] = None


def set_usage_repo(repo: Optional[UsageRepo]) -> None:
    """Wire the usage store in once, at startup (module-level setter)."""
    global _repo
    _repo = repo


def _extract_tokens(usage: Any) -> Optional[dict[str, int]]:
    """Pull token counts off a provider usage object.

    Tolerates OpenAI/Groq style (prompt_tokens / completion_tokens /
    prompt_tokens_details.cached_tokens), Ollama style (prompt_eval_count /
    eval_count) and Anthropic style (input_tokens / output_tokens /
    cache_read_input_tokens), with missing fields defaulting to 0.
    """
    if usage is None:
        return None
    if not isinstance(usage, dict):
        try:
            usage = dict(usage)
        except Exception:
            return None

    details = usage.get("prompt_tokens_details") or {}
    cached = 0
    if isinstance(details, dict):
        cached = details.get("cached_tokens") or 0
    else:
        try:
            cached = getattr(details, "cached_tokens", 0) or 0
        except Exception:
            cached = 0

    inp = usage.get("prompt_tokens") or usage.get("input_tokens") or usage.get("prompt_eval_count")
    out = usage.get("completion_tokens") or usage.get("output_tokens") or usage.get("eval_count")
    cr = usage.get("cache_read_input_tokens") or cached
    cw = usage.get("cache_creation_input_tokens") or 0

    if inp is None and out is None:
        return None
    return {
        "input_tokens": int(inp or 0),
        "output_tokens": int(out or 0),
        "cache_read_tokens": int(cr or 0),
        "cache_write_tokens": int(cw or 0),
    }


def record_usage(model: str, usage: Any, source: str = "conversation") -> None:
    """Record one LLM call's usage as a row.

    ENTIRELY BEST-EFFORT: any failure — repo not wired, malformed usage, DB
    down — is logged and swallowed. This must never raise into or slow the
    conversation path; the insert is a tiny local SQLite write.
    """
    try:
        if _repo is None:
            return
        tokens = _extract_tokens(usage)
        if tokens is None:
            return
        cost = compute_cost(model, **tokens)
        _repo.record(model, cost_usd=cost, source=source, **tokens)
    except Exception:  # noqa: BLE001 — best-effort is the law here
        log.exception("cost: usage recording failed (swallowed)")


# ============================================================== aggregation
def monthly_summary(repo: UsageRepo) -> dict[str, Any]:
    """Month-to-date cost payload for the dashboard.

    - total month-to-date cost / tokens / calls
    - today's spend
    - month-over-month delta vs the same length of the previous month
    - per-model breakdown, cache savings, day-by-day list
    Empty table → a clean zeroed payload, never an error.
    """
    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    month = repo.usage_since(month_start.isoformat(), (month_start + timedelta(days=32)).replace(day=1).isoformat())
    today = repo.usage_since(today_start.isoformat(), (today_start + timedelta(days=1)).isoformat())

    # previous month, same number of days elapsed
    prev_month_start = (month_start - timedelta(days=1)).replace(day=1)
    prev_cutoff = prev_month_start + (now - month_start)
    prev = repo.usage_since(prev_month_start.isoformat(), prev_cutoff.isoformat())

    delta_pct = 0.0
    if prev["cost_usd"] > 0:
        delta_pct = (month["cost_usd"] - prev["cost_usd"]) / prev["cost_usd"] * 100

    return {
        "as_of": now.isoformat(),
        "month": month["cost_usd"],
        "today": today["cost_usd"],
        "delta_pct": round(delta_pct, 1),
        "calls": month["calls"],
        "input_tokens": month["input_tokens"],
        "output_tokens": month["output_tokens"],
        "cache_read_tokens": month["cache_read_tokens"],
        "cache_savings_usd": month["cache_savings_usd"],
        "per_model": month["per_model"],
        "per_day": month["per_day"],
        "per_source": month["per_source"],
        "recent": repo.recent(limit=12),
    }
