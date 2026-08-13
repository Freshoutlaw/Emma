"""The live brief (Tier 4/5) — what the board can see about the business.

The chair must read live figures, every time, and never a number written
down in a file. This builds the brief from the running pipeline's real
stores at meeting time:

- `usage.db` — actual LLM spend, month-to-date, today, per model, per
  source, recent calls. Always available; never a file-cached number.
- Business figures — two live feeds, both sourced:
    1. What the operator actually told Emma: business numbers land in
       episodic memory, get extracted with provenance (episode id + date),
       and are shown labeled "reported to Emma" — never as verified
       financials.
    2. A real business database via `EMMA_SUPABASE_QUERY_DSN`: when set,
       deterministic read-only probes pull likely revenue/subscription
       rows at meeting time and the actual rows are shown.
  When neither feed has data, the brief says so and says exactly how to
  fix it — it never invents a number.

Seats get a short brief (headline numbers only); the chair gets the full
brief. The chair reconciles the seats against the full picture.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from board.metrics import (
    BusinessMetrics,
    extract_from_memory,
    format_metric,
    probe_business_sql,
)


@dataclass
class Brief:
    short: str   # for the seats — enough context to be specific
    full: str    # for the chair — everything


async def build_brief(pipeline: Any) -> Brief:
    try:
        monthly = pipeline.usage_repo.monthly_summary(pipeline.usage_repo)
    except Exception:
        monthly = {}

    month_cost = float(monthly.get("month") or 0.0)
    today_cost = float(monthly.get("today") or 0.0)
    calls = int(monthly.get("calls") or 0)
    delta = float(monthly.get("delta_pct") or 0.0)
    top_models = monthly.get("per_model") or []
    per_source = monthly.get("per_source") or []
    recent = monthly.get("recent") or []

    def money(v: float) -> str:
        return f"${v:,.2f}"

    # ---- business figures (live, sourced) -----------------------------
    # Extraction is freshness-gated internally: no LLM call, no new
    # operator episodes since the last extraction.
    try:
        await extract_from_memory(pipeline)
    except Exception:
        pass
    metrics_store = BusinessMetrics(pipeline.settings.data_dir / "business.db")
    metrics = metrics_store.snapshot()

    reported_lines = [
        f"    {m['metric']} = {format_metric(m['value'], m['unit'])} "
        f"(source {m['source']}, as of {str(m['learned_ts'])[:16]})"
        for m in metrics
    ]

    probe_lines: list[str] = []
    try:
        probe_lines = await probe_business_sql(pipeline)
    except Exception:
        probe_lines = []

    dsn = getattr(pipeline.settings, "supabase_query_dsn", None)

    if reported_lines:
        business_short = (
            "Reported business figures (what you told Emma): "
            + ", ".join(
                f"{m['metric']} {format_metric(m['value'], m['unit'])}"
                for m in metrics[:3]
            )
            + "."
        )
        business_full = [
            "- Business figures (as reported to Emma — sourced, not verified financials):",
            *reported_lines,
        ]
    elif probe_lines:
        business_short = "Business database connected — the chair reads live figures mid-meeting."
        business_full = [
            "- Business database (live SQL, read this meeting):",
            *probe_lines,
        ]
    elif dsn:
        business_short = "Business database connected but no metric tables found by the probe."
        business_full = [
            "- Business database: CONNECTED (EMMA_SUPABASE_QUERY_DSN set), but the probe found "
            "no tables whose names suggest revenue/subscription figures. The chair may run its "
            "own read-only SELECT via the supabase_query agent if it knows the schema.",
        ]
    else:
        business_short = "No business figures available yet."
        business_full = [
            "- Business figures: none available yet. Tell Emma a number (e.g. \"our MRR is "
            "$4,200\") and it is read live from memory at the next meeting, or set "
            "EMMA_SUPABASE_QUERY_DSN to a read-only business database. Until one of those is "
            "true, DO NOT invent figures; say so when they matter.",
        ]

    short_lines = [
        f"LLM spend this month: {money(month_cost)} across {calls} calls "
        f"(today: {money(today_cost)}).",
        business_short,
    ]
    full_lines = [
        "LIVE BUSINESS BRIEF (read fresh from Emma's stores at meeting time):",
        f"- LLM spend month-to-date: {money(month_cost)} ({calls} calls; "
        f"{delta:+.1f}% vs the same span last month; today {money(today_cost)}).",
        "- Top models: " + (
            ", ".join(
                f"{m.get('model')} {money(float(m.get('cost_usd') or 0))}" for m in top_models[:4]
            ) or "none recorded."
        ),
        "- Spend by source: " + (
            ", ".join(
                f"{s.get('source')} {money(float(s.get('cost_usd') or 0))} ({s.get('calls', 0)} calls)"
                for s in per_source[:5]
            ) or "none."
        ),
        "- Recent calls: " + (
            ", ".join(
                f"{r.get('ts', '')[:16]} {r.get('model')} {money(float(r.get('cost_usd') or 0))}"
                for r in recent[:5]
            ) or "none."
        ),
        *business_full,
    ]
    try:
        episodes = pipeline.episodic.count()
        full_lines.append(f"- Episodic memory: {episodes} stored episodes.")
    except Exception:
        pass

    return Brief(short="\n".join(short_lines), full="\n".join(full_lines))
