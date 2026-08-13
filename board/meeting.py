"""The meeting (Tier 4) — fan out, in isolation.

Each seat gets ONE model call whose system prompt contains that seat's
dossier and nothing else. It does not know who else is in the room — no
"you are playing the following five advisors" ever exists in this code.

Money guards:
- Generous, explicit output limits (a truncated response is a paid-for
  meeting that returned nothing).
- A per-meeting cost ceiling checked BEFORE the fan-out starts and before
  every call. A ceiling of zero means zero: no call is ever made.
- Seats are de-duplicated before calling: one advisor named twice never
  buys two calls and never produces two identical opinions.
- A seat may fail without failing the meeting — three good opinions and
  one timeout is a partial meeting, not a dead one.

Every model field is treated as hostile input (coerced in models.py):
identity, not truthiness.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from board.citations import filter_citations, is_unsourced
from board.models import (
    DoctrineEntry,
    MeetingResult,
    Seat,
    SeatOpinion,
    coerce_bool,
    coerce_float,
    coerce_str,
    coerce_str_list,
)

log = logging.getLogger(__name__)

SEAT_MAX_TOKENS = 1200
CHAIR_MAX_TOKENS = 2000
SEAT_TIMEOUT_S = 90.0
CHAIR_TIMEOUT_S = 120.0

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def extract_json_object(text: str) -> Optional[dict]:
    """Extract the first JSON object from free-form model text."""
    if not text:
        return None
    match = _JSON_RE.search(text)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        # Try to salvage a JSON with trailing commas / stray text.
        try:
            data = json.loads(re.sub(r",\s*([}\]])", r"\1", match.group(0)))
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            return None


def _dossier_markdown(seat: Seat, shown: list[DoctrineEntry]) -> str:
    """Render exactly what the seat is shown — doctrine, sources, objection,
    blind spots, voice. Nothing about any other seat."""
    lines = [
        f"You are advising as {seat.name}, seat: {seat.seat_title}.",
        "",
        "Your standing domains: " + ", ".join(seat.domains) + ".",
        "",
    ]
    if seat.characteristic_objection:
        lines += ["What you reliably push back on:", seat.characteristic_objection, ""]
    if seat.voice:
        lines += ["Your voice:", seat.voice, ""]
    if seat.blind_spots:
        lines += ["Where your doctrine does NOT transfer (be honest about these):"]
        lines += [f"- {b}" for b in seat.blind_spots]
        lines += [""]
    lines += ["Your doctrine — numbered entries. You may cite ONLY these ids:", ""]
    for entry in shown:
        status_note = " (you authored this yourself)" if entry.status == "user" else ""
        lines += [
            f"- {entry.id} — {entry.title}{status_note}",
            f"  {entry.content}",
            f"  Source: {entry.source}",
            "",
        ]
    return "\n".join(lines)


_SEAT_OUTPUT_SCHEMA = """Answer with ONLY a JSON object, no prose around it:
{
  "position": "your position in one or two sentences",
  "reasoning": "your reasoning, grounded in your doctrine",
  "citations": ["D1", "D2"],   // doctrine ids from YOUR list above, or []
  "confidence": 0.8,            // 0.0 to 1.0
  "would_change_mind": "what evidence or condition would change your position",
  "abstain": false              // true only if you genuinely have no doctrine on this
}
Rules:
- Cite ONLY the doctrine ids listed above. Do not invent ids, cite sources by name, or quote documents.
- If you have no relevant doctrine, set abstain to true and give no position.
- Do not role-play anyone else. Do not speculate about what other advisors think."""


async def _call_seat(
    llm: Any,
    seat: Seat,
    shown: list[DoctrineEntry],
    question: str,
    brief_short: str,
    max_tokens: int,
    timeout_s: float,
) -> SeatOpinion:
    messages = [
        {"role": "system", "content": _dossier_markdown(seat, shown)},
        {
            "role": "user",
            "content": (
                f"The operator asks the board:\n\n{question}\n\n"
                f"Business context (brief):\n{brief_short}\n\n"
                f"{_SEAT_OUTPUT_SCHEMA}"
            ),
        },
    ]
    raw = await asyncio.wait_for(
        llm(messages, temperature=0.3, max_tokens=max_tokens),
        timeout=timeout_s,
    )
    return _parse_seat_opinion(seat, raw)


def _parse_seat_opinion(seat: Seat, raw: str) -> SeatOpinion:
    shown_ids = seat.doctrine_ids()
    data = extract_json_object(raw) or {}
    cited_raw = coerce_str_list(data.get("citations"))
    valid, rejected = filter_citations(cited_raw, shown_ids)
    abstain = coerce_bool(data.get("abstain"))
    opinion = SeatOpinion(
        seat_id=seat.id,
        seat_name=seat.name,
        position=coerce_str(data.get("position")),
        reasoning=coerce_str(data.get("reasoning")),
        citations=valid,
        citations_rejected=rejected,
        confidence=coerce_float(data.get("confidence")),
        would_change_mind=coerce_str(data.get("would_change_mind")),
        abstain=abstain,
        unsourced=is_unsourced(valid, abstain),
        raw=raw[:4000],
    )
    if data is None or not (opinion.position or opinion.abstain):
        opinion.error = "unparseable response (no position, no abstention)"
    return opinion


def _snapshot_usage_cost(repo: Any, start_iso: str) -> float:
    """Cost accrued in usage.db since start_iso — real money, not estimates."""
    try:
        now = datetime.now(timezone.utc).isoformat()
        agg = repo.usage_since(start_iso, now)
        return float(agg.get("cost_usd") or 0.0)
    except Exception:
        return 0.0


class MeetingRunner:
    """Fan-out orchestrator. Owns the budget and the tolerance."""

    def __init__(
        self,
        llm: Any,
        usage_repo: Any,
        cost_ceiling_usd: float = 0.50,
        max_seats: int = 4,
    ) -> None:
        self._llm = llm
        self._usage_repo = usage_repo
        self.cost_ceiling_usd = float(cost_ceiling_usd)
        self.max_seats = max_seats

    async def run(
        self,
        question: str,
        seats: list[Seat],
        brief_full: str,
        brief_short: str,
        prompted: bool = True,
    ) -> MeetingResult:
        meeting_id = uuid.uuid4().hex[:12]
        ts = datetime.now(timezone.utc).isoformat()
        result = MeetingResult(
            meeting_id=meeting_id,
            ts=ts,
            question=question,
            prompted=prompted,
            seats=[s.id for s in seats],
            brief=brief_full,
        )

        # Ceiling of zero means zero: refuse before a single call.
        if self.cost_ceiling_usd <= 0:
            result.error = "board spend ceiling is zero — no meeting convened"
            return result

        # De-duplicate seats before calling — one advisor named twice must
        # not buy two calls or produce two identical opinions.
        unique: list[Seat] = []
        seen: set[str] = set()
        for seat in seats[: self.max_seats]:
            if seat.id in seen:
                continue
            seen.add(seat.id)
            unique.append(seat)

        start_iso = datetime.now(timezone.utc).isoformat()
        spent = _snapshot_usage_cost(self._usage_repo, start_iso)

        async def run_one(seat: Seat) -> SeatOpinion:
            nonlocal spent
            # Per-call budget check BEFORE the call.
            if spent >= self.cost_ceiling_usd:
                return SeatOpinion(
                    seat_id=seat.id,
                    seat_name=seat.name,
                    error="skipped — meeting cost ceiling reached",
                )
            shown = [e for e in seat.doctrine if not e.retired]
            try:
                opinion = await _call_seat(
                    self._llm, seat, shown, question, brief_short,
                    SEAT_MAX_TOKENS, SEAT_TIMEOUT_S,
                )
                spent = _snapshot_usage_cost(self._usage_repo, start_iso)
                return opinion
            except asyncio.TimeoutError:
                return SeatOpinion(
                    seat_id=seat.id, seat_name=seat.name,
                    error=f"timeout after {SEAT_TIMEOUT_S}s",
                )
            except Exception as exc:  # one seat failing never kills the meeting
                log.exception("board: seat %s failed", seat.id)
                return SeatOpinion(seat_id=seat.id, seat_name=seat.name, error=str(exc)[:300])

        opinions = list(await asyncio.gather(*(run_one(s) for s in unique)))
        result.opinions = opinions
        result.cost_usd = _snapshot_usage_cost(self._usage_repo, start_iso)
        return result
