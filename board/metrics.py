"""Live business figures for the board — real data, sourced, never invented.

Two feeds feed the chair's brief:

1. SQL probe — when ``EMMA_SUPABASE_QUERY_DSN`` is set, deterministic
   read-only queries (validated by the existing SupabaseQueryAgent) pull
   likely revenue/subscription-style rows from the business database at
   meeting time. The brief shows the actual rows the chair saw.

2. Told-to-Emma facts — business numbers the operator actually gave Emma
   live in chat land in episodic memory. At meeting time we extract them
   with one bounded, adversarial-style LLM call that MUST attach the
   episode id each figure came from (provenance), then cache them here.
   The brief labels these "reported to Emma" — never as verified
   financials — and the chair can say *which episode* a number rests on.

Extraction is freshness-gated: no LLM call when no new operator episodes
exist since the last extraction, so a meeting never pays for nothing.
Every model-supplied scalar is coerced by identity (hostile-input rule);
a metric without a matching episode id, or outside the vocabulary, is
dropped with a log line — never guessed.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from board.models import coerce_bool, coerce_float, coerce_str, coerce_str_list

log = logging.getLogger(__name__)

EXTRACT_TIMEOUT_S = 45.0
EXTRACT_MAX_TOKENS = 700
EXTRACT_EPISODE_LIMIT = 25          # most recent operator episodes considered
RECENT_WINDOW = 80                  # episodes pulled from memory each check

# A controlled vocabulary: extraction accepts only these metric names, so
# the store never fills with junk.  The LLM is told the vocabulary up front.
METRIC_VOCABULARY = {
    "revenue", "mrr", "arr", "subscribers", "customers", "users",
    "churn_pct", "cac", "ltv", "margin_pct", "conversion_pct",
    "price", "profit", "burn", "runway_months", "pipeline_value", "deals",
    "costs",
}
UNITS = {"usd", "count", "pct", "months"}

_METRIC_SQL_TABLE_RE = re.compile(
    r"(revenue|subscri|customer|sale|invoice|mrr|churn|account|project|plan|"
    r"order|payment|metric|kpi)",
    re.IGNORECASE,
)

_EXTRACT_SYSTEM = (
    "You extract business metrics from what an operator told an AI assistant. "
    "You are given a list of the operator's statements, each with an episode id.\n"
    "Extract ONLY concrete, stated numbers: revenue, MRR, ARR, subscribers, "
    "customers, users, churn %, CAC, LTV, margin %, conversion %, price, profit, "
    "burn, runway (months), pipeline value, deals, costs.\n"
    "Rules:\n"
    "- Every extracted item MUST carry the episode_id of the statement it came from. "
    "Use only episode ids from the provided list.\n"
    "- Skip anything that is not a concrete number (\"growing\", \"about to double\" — skip).\n"
    "- Skip speculation, goals, or the assistant's own summaries.\n"
    "- If two statements give different values for the same metric, extract both "
    "with their own episode ids — do not average or pick.\n"
    "- unit must be one of: usd, count, pct, months.\n"
    "Answer with ONLY a JSON array, no prose:\n"
    '[{"metric": "mrr", "value": 4200, "unit": "usd", "episode_id": "abc123", '
    '"note": "told in chat, monthly recurring revenue"}]'
)


class BusinessMetrics:
    """Append-only store of sourced business figures (data/business.db)."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), timeout=20.0)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS business_metrics (
                metric     TEXT NOT NULL,
                value      REAL NOT NULL,
                unit       TEXT NOT NULL DEFAULT '',
                source     TEXT NOT NULL,      -- 'episode:<id>' | 'sql:<table>' | 'user'
                learned_ts TEXT NOT NULL,
                note       TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS meta (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )

    # -------------------------------------------------------------- writes
    def record(
        self,
        metric: str,
        value: float,
        unit: str,
        source: str,
        learned_ts: str,
        note: str = "",
    ) -> None:
        metric = metric.strip().lower()
        if metric not in METRIC_VOCABULARY:
            log.warning("board metrics: metric '%s' outside vocabulary — dropped", metric)
            return
        unit = unit.strip().lower() if unit.strip().lower() in UNITS else "usd"
        self._conn.execute(
            "INSERT INTO business_metrics (metric, value, unit, source, learned_ts, note) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (metric, coerce_float(value), unit, source, learned_ts, note),
        )
        self._conn.commit()

    def set_meta(self, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        self._conn.commit()

    def get_meta(self, key: str) -> Optional[str]:
        row = self._conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    # --------------------------------------------------------------- reads
    def snapshot(self) -> list[dict]:
        """Latest value per metric, newest first, with provenance."""
        rows = self._conn.execute(
            "SELECT * FROM business_metrics ORDER BY learned_ts DESC, rowid DESC"
        ).fetchall()
        seen: set[str] = set()
        latest: list[dict] = []
        for r in rows:
            if r["metric"] in seen:
                continue
            seen.add(r["metric"])
            latest.append(dict(r))
        latest.sort(key=lambda d: d["metric"])
        return latest

    def has_data(self) -> bool:
        return self._conn.execute("SELECT COUNT(*) FROM business_metrics").fetchone()[0] > 0

    def last_extract_ts(self) -> Optional[str]:
        return self.get_meta("last_extract_ts")


def format_metric(value: float, unit: str) -> str:
    unit = (unit or "usd").lower()
    if unit == "usd":
        return f"${value:,.2f}"
    if unit == "pct":
        return f"{value:g}%"
    if unit == "months":
        return f"{value:g} months"
    return f"{value:g}"


# ------------------------------------------------------------ extraction
def _extract_json(text: str) -> Optional[list]:
    """Parse the extraction response — JSON array, possibly wrapped in prose."""
    m = re.search(r"\[.*\]", text or "", re.DOTALL)
    if not m:
        return None
    try:
        data = json.loads(re.sub(r",\s*([}\]])", r"\1", m.group(0)))
        return data if isinstance(data, list) else None
    except json.JSONDecodeError:
        return None


async def extract_from_memory(pipeline: Any) -> int:
    """Extract operator-stated business figures from episodic memory.

    Freshness-gated: returns 0 (no LLM call) when there are no operator
    episodes newer than the last extraction. Every recorded figure carries
    the episode id it came from — the chair can always trace it.
    """
    store = BusinessMetrics(pipeline.settings.data_dir / "business.db")
    episodes = pipeline.episodic.recent(limit=RECENT_WINDOW)
    last = store.last_extract_ts()
    fresh = [
        e for e in episodes
        if (e or {}).get("kind") == "user"
        and (last is None or (e.get("ts") or "") > last)
    ][:EXTRACT_EPISODE_LIMIT]
    if not fresh:
        return 0

    sent_ids = {e.get("id") for e in fresh}
    payload_lines = []
    for e in fresh:
        content = (e.get("content") or "").strip().replace("\n", " ")
        if not content:
            continue
        payload_lines.append(f"- [{e.get('id')}] ({e.get('ts', '')[:16]}) {content[:600]}")
    if not payload_lines:
        return 0
    payload = "\n".join(payload_lines)

    try:
        raw = await asyncio.wait_for(
            pipeline.llm.complete(
                [
                    {"role": "system", "content": _EXTRACT_SYSTEM},
                    {"role": "user", "content": payload},
                ],
                temperature=0.0,
                max_tokens=EXTRACT_MAX_TOKENS,
            ),
            timeout=EXTRACT_TIMEOUT_S,
        )
    except Exception as exc:
        log.warning("board metrics: extraction call failed (%s) — no figures recorded", str(exc)[:120])
        store.set_meta("last_extract_ts", datetime.now(timezone.utc).isoformat())
        return 0

    data = _extract_json(raw)
    if data is None:
        log.warning("board metrics: extraction response did not parse — no figures recorded")
        store.set_meta("last_extract_ts", datetime.now(timezone.utc).isoformat())
        return 0

    now = datetime.now(timezone.utc).isoformat()
    recorded = 0
    for item in data:
        if not isinstance(item, dict):
            continue
        episode_id = coerce_str(item.get("episode_id"))
        if episode_id not in sent_ids:
            log.warning(
                "board metrics: extracted episode_id %r not in shown set — dropped (fabrication guard)",
                episode_id,
            )
            continue
        learned_ts = next((e.get("ts") for e in fresh if e.get("id") == episode_id), now)
        store.record(
            metric=coerce_str(item.get("metric")),
            value=coerce_float(item.get("value")),
            unit=coerce_str(item.get("unit")),
            source=f"episode:{episode_id}",
            learned_ts=learned_ts,
            note=coerce_str(item.get("note"))[:300],
        )
        recorded += 1

    store.set_meta("last_extract_ts", now)
    if recorded:
        log.info("board metrics: recorded %d figure(s) from episodic memory", recorded)
    return recorded


# ------------------------------------------------------------ SQL probe
async def probe_business_sql(pipeline: Any) -> list[str]:
    """Live read-only probe of the business DB (only when DSN configured).

    Lists tables, picks likely metric tables by name, and reads up to 5 rows
    from the first three — the actual rows the chair saw, with the SQL shown.
    """
    agent = getattr(pipeline, "supabase_query_agent", None)
    if agent is None or not getattr(agent, "is_configured", lambda: False)():
        return []

    lines: list[str] = []
    try:
        tables_result = await agent.list_tables()
        if not getattr(tables_result, "ok", False):
            return []
        try:
            tables = json.loads(tables_result.output or "[]")
        except json.JSONDecodeError:
            return []
        candidates = [t.get("name") for t in tables if t.get("name") and _METRIC_SQL_TABLE_RE.search(t["name"])]
    except Exception as exc:
        log.warning("board metrics: SQL probe failed at table listing (%s)", str(exc)[:120])
        return []

    for table in candidates[:3]:
        try:
            result = await agent.query(f'SELECT * FROM "{table}" LIMIT 5')
            if not getattr(result, "ok", False):
                continue
            try:
                rows = json.loads(result.output or "[]")
            except json.JSONDecodeError:
                rows = []
            if rows:
                lines.append(f"    {table}: {json.dumps(rows[:5], default=str)[:600]}")
        except Exception as exc:
            log.warning("board metrics: SQL probe failed on %s (%s)", table, str(exc)[:120])
    return lines
