"""Board data model — doctrine entries, seats, opinions, meetings.

Every scalar that arrives from a model is coerced here (never trusted raw):
a boolean that arrives as the string ``"false"`` must be identity-checked,
not truthiness-checked, or a seat "abstaining" when it didn't would be told
to the operator as a real abstention.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class DoctrineEntry:
    """One numbered, sourced principle. The id is explicit in the file and
    is spoken for forever — retirement reuses nothing."""

    id: str                 # e.g. "D1"
    title: str
    content: str
    source: str
    status: str = "sourced"  # "sourced" (fact-checked) | "user" (operator-authored)
    retired: bool = False    # retired ids are withheld from seats, never deleted


@dataclass
class Seat:
    """A parsed dossier. `domains` drives routing; `doctrine` is what a seat
    may cite — nothing else."""

    id: str
    name: str
    seat_title: str
    domains: list[str] = field(default_factory=list)
    status: str = "active"
    characteristic_objection: str = ""
    blind_spots: list[str] = field(default_factory=list)
    voice: str = ""
    doctrine: list[DoctrineEntry] = field(default_factory=list)
    source_file: str = ""

    def doctrine_ids(self) -> set[str]:
        return {e.id for e in self.doctrine if not e.retired}


@dataclass
class SeatOpinion:
    """One seat's structured answer. All model-supplied scalars coerced."""

    seat_id: str
    seat_name: str
    position: str = ""
    reasoning: str = ""
    citations: list[str] = field(default_factory=list)   # post-gate, valid ids only
    citations_rejected: list[str] = field(default_factory=list)
    confidence: float = 0.0
    would_change_mind: str = ""
    abstain: bool = False
    unsourced: bool = False      # spoke without citing anything and didn't abstain
    error: Optional[str] = None  # call failed / timed out — seat still counts
    raw: str = ""                # whatever the model actually returned


@dataclass
class ChairVerdict:
    unanimous: bool
    split: list[str]             # short per-seat position labels
    synthesis: str = ""
    spoken_summary: str = ""
    discount_notes: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)   # e.g. "D2 rests on a user entry"
    guard_notes: list[str] = field(default_factory=list)


@dataclass
class MeetingResult:
    meeting_id: str
    ts: str
    question: str
    prompted: bool = True
    seats: list[str] = field(default_factory=list)
    opinions: list[SeatOpinion] = field(default_factory=list)
    verdict: Optional[ChairVerdict] = None
    brief: str = ""
    cost_usd: float = 0.0
    declined: bool = False
    decline_reason: str = ""
    error: Optional[str] = None

    def to_payload(self, citation_map: Optional[dict[str, dict]] = None) -> dict[str, Any]:
        """Serializable payload for the HUD + storage.

        ``citation_map``: seat_id -> {citation_id: {title, source}} — the
        snapshot taken at meeting time so a stored meeting renders its own
        citations without re-reading the dossier.
        """
        citation_map = citation_map or {}
        return {
            "meeting_id": self.meeting_id,
            "ts": self.ts,
            "question": self.question,
            "prompted": bool(self.prompted),
            "seats": self.seats,
            "opinions": [
                {
                    "seat_id": o.seat_id,
                    "seat_name": o.seat_name,
                    "position": o.position,
                    "reasoning": o.reasoning,
                    "citations": o.citations,
                    "citations_rejected": o.citations_rejected,
                    "citation_sources": citation_map.get(o.seat_id, {}),
                    "confidence": round(float(o.confidence), 3),
                    "would_change_mind": o.would_change_mind,
                    "abstain": bool(o.abstain),
                    "unsourced": bool(o.unsourced),
                    "error": o.error,
                }
                for o in self.opinions
            ],
            "verdict": None if self.verdict is None else {
                "unanimous": bool(self.verdict.unanimous),
                "split": self.verdict.split,
                "synthesis": self.verdict.synthesis,
                "spoken_summary": self.verdict.spoken_summary,
                "discount_notes": self.verdict.discount_notes,
                "flags": self.verdict.flags,
            },
            "cost_usd": round(float(self.cost_usd), 6),
            "declined": bool(self.declined),
            "decline_reason": self.decline_reason,
            "error": self.error,
        }


# ------------------------------------------------------------------ coercers
def coerce_bool(value: Any, default: bool = False) -> bool:
    """Identity-check a boolean that may arrive as a string.

    ``"false"`` must be False, not truthy; only the literal true-ish values
    count. Anything unrecognized takes the default — we never guess.
    """
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        v = value.strip().lower()
        if v in ("true", "yes", "1", "y", "on"):
            return True
        if v in ("false", "no", "0", "n", "off"):
            return False
    return default


def coerce_float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def coerce_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    if isinstance(value, str):
        return value
    return str(value)


def coerce_str_list(value: Any) -> list[str]:
    """A list may arrive as a JSON list, a comma string, or garbage."""
    if value is None:
        return []
    if isinstance(value, str):
        return [s.strip() for s in value.replace("\n", ",").split(",") if s.strip()]
    if isinstance(value, (list, tuple)):
        return [coerce_str(v) for v in value if coerce_str(v)]
    return []
