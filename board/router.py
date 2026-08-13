"""Routing (Tier 3) — who has standing, and is this a board question at all.

Two things bite here:

1. Normalize apostrophes and unicode before matching names. A dictated
   "O'Leary" arrives with a curly apostrophe (U+2019); a naive comparison
   against O'Leary fails silently and every spoken request for that advisor
   falls through to a paid router call that then has to guess. We handle the
   curly apostrophe, fullwidth variant, non-breaking spaces, and soft
   hyphens.

2. First-name collisions: a bare mention of a shared first name is
   ambiguous and must not route. We match on surnames (or full names) only.

The decline gate is tuned with the concrete-example method: business
decisions about the operator's OWN company are the core use case and are
never declined just because they feel personal. Only code, medical, legal,
health, and family matters are declined — and even then the LLM decides,
because "medical device pricing" is a business question that a keyword
"medical" would wrongly kill.
"""

from __future__ import annotations

import asyncio
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

from board.models import Seat

LLMCall = Callable[[list[dict]], Awaitable[str]]

# Fullwidth and typographic variants we normalize before matching.
_APOSTROPHES = str.maketrans({
    "\u2019": "'",  # curly right single quote
    "\u2018": "'",  # curly left single quote
    "\u02bc": "'",  # modifier apostrophe
    "\uff07": "'",  # fullwidth apostrophe
    "\u201b": "'",  # single high-reversed-9
})
_NBSP = "\u00a0"
_SOFT_HYPHEN = "\u00ad"


def normalize_text(text: str) -> str:
    """NFC + typographic apostrophes + NBSP/soft-hyphen neutralization."""
    text = unicodedata.normalize("NFC", text or "")
    text = text.translate(_APOSTROPHES)
    text = text.replace(_NBSP, " ")
    text = text.replace(_SOFT_HYPHEN, "")
    return text.casefold().strip()


def _surnames(seat: Seat) -> list[str]:
    parts = normalize_text(seat.name).split()
    return parts[1:] or parts  # e.g. "Patrick McKenzie" -> ["mckenzie"]


_DOMAIN_KEYWORDS: dict[str, frozenset[str]] = {
    # domain -> trigger words for that domain (lowercased)
    "offers": frozenset(["offer", "offers", "pricing", "price", "free trial", "guarantee"]),
    "pricing": frozenset(["pricing", "price", "charge", "charges", "subscription", "plan", "plans"]),
    "marketing": frozenset(["marketing", "ads", "advertising", "funnel", "leads", "traffic", "acquisition"]),
    "growth": frozenset(["growth", "scale", "scaling", "expand", "expansion"]),
    "sales": frozenset(["sales", "selling", "close", "closing", "enterprise", "b2b"]),
    "distribution": frozenset(["distribution", "channel", "channels", "platform", "marketplace", "reach"]),
    "strategy": frozenset(["strategy", "strategic", "moat", "competition", "competitive", "positioning", "market"]),
    "operations": frozenset(["operations", "ops", "process", "processes", "workflow", "hiring", "hire", "team", "meeting", "meetings", "focus", "priorities", "roadmap"]),
    "product": frozenset(["product", "features", "feature", "roadmap", "v1", "mvp", "shipping", "ship"]),
    "profitability": frozenset(["profit", "profitability", "margin", "margins", "cost", "costs", "burn", "spend", "expenses"]),
    "churn": frozenset(["churn", "retention", "cancel", "cancellations", "turnover"]),
    "revenue": frozenset(["revenue", "revenues", "mrr", "arr", "income", "numbers", "growth rate"]),
    "sustainability": frozenset(["sustainable", "calm", "workload", "burnout", "hours", "balance", "sanity"]),
}


def _domain_hits(question_norm: str) -> set[str]:
    hits: set[str] = set()
    for domain, words in _DOMAIN_KEYWORDS.items():
        if any(w in question_norm for w in words):
            hits.add(domain)
    return hits


@dataclass
class TriageResult:
    declined: bool = False
    decline_reason: str = ""
    seats: list[Seat] = field(default_factory=list)
    matched_by: dict[str, str] = field(default_factory=dict)  # seat_id -> how


@dataclass
class _DeclineRule:
    pattern: re.Pattern
    reason: str


# Fast-path hard declines — ONLY unambiguous, non-business subjects. Anything
# ambiguous goes to the LLM, because the board must never refuse "should I
# cut this product loose?" — the word "personal" is NOT a decline trigger.
_FAST_DECLINE = (
    _DeclineRule(re.compile(r"\b(diagnos|symptom|prescription|medication|tumor|surgery|therapist|psychiatr|depression|anxiety|headache|doctor|injury)\b", re.I),
                 "medical diagnosis or treatment — outside the board's remit"),
    _DeclineRule(re.compile(r"\b(legal advice|lawsuit|sue|suing|attorney[- ]client|contract dispute)\b", re.I),
                 "legal advice — outside the board's remit"),
    _DeclineRule(re.compile(r"\b(divorce|marriage counseling|my wife|my husband|my partner should|should i leave)\b", re.I),
                 "family or relationship matters — outside the board's remit"),
)

_DECLINE_SYSTEM_PROMPT = (
    "You are the triage gate for a board of business advisors. Decide whether a "
    "question belongs to the board or should be declined.\n"
    "The board EXISTS for business decisions about the operator's own company — "
    "pricing, hiring, product direction, cutting or keeping a product, spending, "
    "strategy, marketing, operations. These are the CORE use case and are never "
    "declined, no matter how personal they feel ('it's my company' is not a reason "
    "to decline; it is the reason to accept).\n"
    "Decline ONLY for these concrete cases:\n"
    "- medical diagnosis or treatment (symptoms, medication, therapy)\n"
    "- legal advice (lawsuits, contracts, liability)\n"
    "- tax or accounting specifics requiring a licensed professional\n"
    "- family or relationship matters (divorce, marriage, children)\n"
    "- code review or implementation work (that's the coder's job, not the board's)\n"
    "Answer with ONLY JSON: {\"decline\": true or false, \"reason\": \"short reason or empty\"}"
)


class BoardRouter:
    def __init__(self, llm: LLMCall, max_seats: int = 4) -> None:
        self._llm = llm
        self.max_seats = max_seats

    # ------------------------------------------------------------ triage
    async def triage(self, question: str, roster: list[Seat]) -> TriageResult:
        normalized = normalize_text(question)
        # 1. Fast-path declines.
        for rule in _FAST_DECLINE:
            if rule.pattern.search(question):
                return TriageResult(declined=True, decline_reason=rule.reason)
        # 2. LLM decline check (one cheap, bounded call).
        if self._llm is not None:
            try:
                text = await asyncio.wait_for(
                    self._llm(
                        [
                            {"role": "system", "content": _DECLINE_SYSTEM_PROMPT},
                            {"role": "user", "content": question},
                        ],
                        temperature=0.0,
                        max_tokens=120,
                    ),
                    timeout=20,
                )
                verdict = self._parse_decline_json(text)
                if verdict is not None and verdict[0]:
                    return TriageResult(declined=True, decline_reason=verdict[1] or "outside the board's remit")
            except Exception:
                pass  # decline check fails open to the seat selection; the
                # citation gate is where correctness is enforced, not here.
        # 3. Seat selection.
        seats, matched = self._select_seats(normalized, roster)
        return TriageResult(seats=seats, matched_by=matched)

    # ------------------------------------------------------------ seats
    def _select_seats(self, question_norm: str, roster: list[Seat]) -> tuple[list[Seat], dict[str, str]]:
        """Match by surname/full name first, then by domain standing.

        Name matches are exact and unambiguous (surname required). Domain
        matches use the seat's declared domains against trigger words, so a
        seat never participates without either being named or having stated
        standing.
        """
        selected: list[Seat] = []
        matched: dict[str, str] = {}
        domain_hits = _domain_hits(question_norm)

        for seat in roster:
            if seat.status != "active":
                continue
            # Explicit name mention (surname or full name).
            name_hit = False
            for surname in _surnames(seat):
                if surname and surname in question_norm:
                    name_hit = True
                    break
            if name_hit:
                selected.append(seat)
                matched[seat.id] = "named"
                continue
            # Domain standing.
            if any(dom in seat.domains for dom in domain_hits):
                selected.append(seat)
                matched[seat.id] = f"domain:{','.join(sorted(domain_hits & set(seat.domains)))}"

        return selected[: self.max_seats], matched

    # ------------------------------------------------------------ decline parse
    @staticmethod
    def _parse_decline_json(text: str) -> Optional[tuple[bool, str]]:
        from board.meeting import extract_json_object
        from board.models import coerce_bool, coerce_str
        data = extract_json_object(text)
        if data is None:
            return None
        decline = coerce_bool(data.get("decline"))
        reason = coerce_str(data.get("reason"))
        return (decline, reason)
