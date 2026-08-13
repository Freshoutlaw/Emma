"""The live brief (Tier 4/5) — what the board can see about the business.

The chair must read live figures, every time, and never a number written
down in a file. This builds the brief from the running pipeline's real
stores at meeting time:

- `usage.db` — actual LLM spend, month-to-date, today, per model, per
  source, recent calls. Always available; never a file-cached number.
- Business metrics — if EMMA_SUPABASE_QUERY_DSN is configured, the chair
  can run read-only SQL against the business database mid-meeting; the
  brief says so explicitly. When it is unset (as it is in this repo), the
  brief says revenue figures are unavailable rather than pretending.

Seats get a short brief (headline numbers only); the chair gets the full
brief. The chair reconciles the seats against the full picture.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class Brief:
    short: str   # for the seats — enough context to be specific
    full: str    # for the chair — everything


def build_brief(pipeline: Any) -> Brief:
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

    short_lines = [
        f"LLM spend this month: {money(month_cost)} across {calls} calls "
        f"(today: {money(today_cost)}).",
    ]
    full_lines = [
        "LIVE BUSINESS BRIEF (read fresh from Emma's stores at meeting time):",
        f"- LLM spend month-to-date: {money(month_cost)} ({calls} calls; "
        f"{delta:+.1f}% vs the same span last month; today {money(today_cost)}).",
        "- Top models: " + (", ".join(
            f"{m.get('model')} {money(float(m.get('cost_usd') or 0))}" for m in top_models[:4]
        ) or "none recorded."),
        "- Spend by source: " + (", ".join(
            f"{s.get('source')} {money(float(s.get('cost_usd') or 0))} ({s.get('calls', 0)} calls)"
            for s in per_source[:5]
        ) or "none."),
        "- Recent calls: " + (", ".join(
            f"{r.get('ts', '')[:16]} {r.get('model')} {money(float(r.get('cost_usd') or 0))}"
            for r in recent[:5]
        ) or "none."),
    ]
    try:
        episodes = pipeline.episodic.count()
        full_lines.append(f"- Episodic memory: {episodes} stored episodes.")
    except Exception:
        pass

    dsn = getattr(pipeline.settings, "supabase_query_dsn", None)
    if dsn:
        full_lines.append(
            "- Business database: CONNECTED (EMMA_SUPABASE_QUERY_DSN set). "
            "You may run read-only SQL (SELECT/WITH, max 100 rows) via the "
            "supabase_query agent to pull revenue, subscriptions, churn, or "
            "pipeline figures before synthesizing."
        )
    else:
        full_lines.append(
            "- Business database: NOT CONFIGURED (EMMA_SUPABASE_QUERY_DSN is "
            "unset). Revenue / churn / pipeline figures are UNAVAILABLE to "
            "this meeting. Do not invent them; say so when they matter."
        )

    return Brief(short="\n".join(short_lines), full="\n".join(full_lines))
