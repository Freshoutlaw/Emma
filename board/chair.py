"""The chair (Tier 5) — your agent, the only participant who read the numbers.

The seats are well-read but blind; the chair reads the full live brief and
every opinion with its citations, and is told each seat's blind spots.

Two guards are deterministic, not prompted:

1. Unanimity guard — fewer than two non-abstaining seats can never be
   reported as unanimous. One voice is not a consensus, and a model will
   cheerfully call it one.

2. Spoken-summary guard — the spoken sentence must match the computed
   verdict. This fired once with the guard working perfectly: the verdict
   said not-unanimous and the prose said "the board is unanimous" anyway.
   So we check the words and refuse to let them disagree.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from board.meeting import CHAIR_MAX_TOKENS, CHAIR_TIMEOUT_S, extract_json_object
from board.models import ChairVerdict, Seat, SeatOpinion, coerce_bool, coerce_str, coerce_str_list

log = logging.getLogger(__name__)

_CHAIR_SYSTEM_PROMPT = (
    "You are Emma's chair of the board of advisors. You are the only participant "
    "who has read the full business brief. The seats have given their opinions "
    "in isolation, each citing only their own numbered doctrine.\n"
    "Your job:\n"
    "1. NAME THE SPLIT BEFORE THE AGREEMENT. Where the board divided is the "
    "information; where it agreed is often just the obvious.\n"
    "2. Discount seats using their DOCUMENTED blind spots — when a position "
    "falls inside one, say so plainly.\n"
    "3. Treat abstention as abstention, never as assent.\n"
    "4. Flag positions that rest on entries the operator authored themselves "
    "(marked 'user') as the operator's own assumption, not documented doctrine.\n"
    "5. The spoken summary must be ONE or TWO sentences, lead with the split, "
    "and must never claim unanimity unless the verdict is genuinely unanimous "
    "among at least two non-abstaining seats.\n"
    "Answer with ONLY JSON:\n"
    "{\n"
    "  \"split\": [\"seat-name: one-line position\", ...],\n"
    "  \"synthesis\": \"the full written synthesis (several paragraphs)\",\n"
    "  \"spoken_summary\": \"1-2 sentences for voice, leading with the split\",\n"
    "  \"discounts\": [\"e.g. Fried's position falls inside his stated blind spot on venture-scale growth\", ...],\n"
    "  \"flags\": [\"e.g. patio11's D1 rests on a user-authored entry\", ...]\n"
    "}"
)


def _seat_block(seat: Seat, opinion: SeatOpinion) -> str:
    blind = "; ".join(seat.blind_spots) or "none documented"
    cites = ", ".join(opinion.citations) or "(none)"
    return (
        f"--- {seat.name} ({seat.seat_title}) ---\n"
        f"Blind spots: {blind}\n"
        f"Position: {opinion.position or '(abstained / no position)'}\n"
        f"Reasoning: {opinion.reasoning or '(none)'}\n"
        f"Citations: {cites}   Confidence: {opinion.confidence:.2f}\n"
        f"Would change mind if: {opinion.would_change_mind or '(not stated)'}\n"
        f"Abstained: {opinion.abstain}   Unsupported (no citations, no abstention): {opinion.unsourced}\n"
    )


def compute_verdict_counts(opinions: list[SeatOpinion]) -> tuple[int, int]:
    """(non_abstaining, total). Errors and abstentions never count as voices."""
    non_abstaining = sum(
        1 for o in opinions if not o.abstain and o.error is None and (o.position or "")
    )
    return non_abstaining, len(opinions)


def can_be_unanimous(opinions: list[SeatOpinion]) -> bool:
    voices, _ = compute_verdict_counts(opinions)
    return voices >= 2


def spoken_summary_matches(verdict: ChairVerdict) -> bool:
    """Guard 2: the words must match the computed verdict."""
    spoken = (verdict.spoken_summary or "").lower()
    if "unanim" in spoken:
        return verdict.unanimous
    return True


class ChairRunner:
    def __init__(self, llm: Any) -> None:
        self._llm = llm

    async def run(
        self,
        question: str,
        seats: list[Seat],
        opinions: list[SeatOpinion],
        brief_full: str,
    ) -> ChairVerdict:
        seat_by_id = {s.id: s for s in seats}
        blocks = []
        for opinion in opinions:
            seat = seat_by_id.get(opinion.seat_id)
            if seat is None:
                continue
            blocks.append(_seat_block(seat, opinion))
        if not blocks:
            return ChairVerdict(unanimous=False, split=[], synthesis="No usable opinions.", spoken_summary="The board could not convene.")

        user_content = (
            f"The operator asked: {question}\n\n"
            f"{brief_full}\n\n"
            "Board opinions (each seat read only its own dossier):\n"
            + "\n".join(blocks)
        )
        try:
            raw = await asyncio.wait_for(
                self._llm(
                    [
                        {"role": "system", "content": _CHAIR_SYSTEM_PROMPT},
                        {"role": "user", "content": user_content},
                    ],
                    temperature=0.3,
                    max_tokens=CHAIR_MAX_TOKENS,
                ),
                timeout=CHAIR_TIMEOUT_S,
            )
        except Exception as exc:
            log.exception("board: chair failed")
            return ChairVerdict(
                unanimous=False,
                split=[f"{o.seat_name}: {o.position[:80]}" for o in opinions if o.position],
                synthesis=f"Chair call failed ({exc}). The opinions above are unmediated.",
                spoken_summary="The chair could not synthesize — see the board panel.",
                guard_notes=["chair call failed"],
            )

        data = extract_json_object(raw) or {}
        unanimous = can_be_unanimous(opinions)
        verdict = ChairVerdict(
            unanimous=unanimous,
            split=coerce_str_list(data.get("split")),
            synthesis=coerce_str(data.get("synthesis")),
            spoken_summary=coerce_str(data.get("spoken_summary")),
            discount_notes=coerce_str_list(data.get("discounts")),
            flags=coerce_str_list(data.get("flags")),
        )

        # Guard 2: never let prose contradict the computed verdict.
        if not spoken_summary_matches(verdict):
            verdict.guard_notes.append("spoken summary claimed unanimity the computed verdict forbids — replaced")
            verdict.spoken_summary = _guard_spoken_summary(opinions, verdict.split)
        if unanimous and not verdict.spoken_summary:
            verdict.guard_notes.append("unanimous verdict with no spoken summary")
        return verdict


def _guard_spoken_summary(opinions: list[SeatOpinion], split: list[str]) -> str:
    voices, _ = compute_verdict_counts(opinions)
    names = [o.seat_name for o in opinions if not o.abstain and o.error is None and o.position]
    if voices < 2:
        return f"Only one seat took a position, so there is no consensus. The board is divided by absence: {', '.join(names) or 'nobody'} spoke."
    return "The board is split." if split else "The board has not reached unanimity."
