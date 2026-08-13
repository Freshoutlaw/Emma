"""Independent adversarial fact-check (Tier 2, stage two).

The researcher who wrote a dossier is never the one who checks it. This pass
is a SEPARATE model call that assumes the file is WRONG and tries to refute
every doctrine entry:

- does the cited source exist as described (title, format, date)?
- does it actually say this, or is it a paraphrase/invention?
- is it THIS person's idea, or something they credited to someone else
  (a framework they attributed, a ratio they publicly disowned)?
- is the framing honest — a narrow finding presented as a general law is a
  defect even when every word is true?

The checker sees only the dossier text — never the researcher's reasoning.
Every scalar it returns is coerced by identity, not truthiness (the same
hostile-input rule as the seats): ``"false"`` as a string is False.

CLI:  python -m board.research.verify [--seat <id>] [--out DIR]
Writes one markdown report per seat (FACTCHECK_<seat_id>.md) so what was
confirmed, corrected, and rejected is visible per entry.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from board.dossier import load_roster
from board.models import Seat, coerce_bool, coerce_str

log = logging.getLogger(__name__)

LLMCall = Callable[[list[dict]], Awaitable[str]]

VERDICTS = ("confirmed", "corrected", "rejected", "unverifiable")

CHECK_TIMEOUT_S = 90.0
CHECK_MAX_TOKENS = 1800

_ARRAY_RE = re.compile(r"\[.*\]", re.DOTALL)
_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def _salvage(text: str) -> str:
    """Strip trailing commas so a sloppy model JSON still parses."""
    return re.sub(r",\s*([}\]])", r"\1", text)


def extract_json_array(text: str) -> Optional[list]:
    """Extract a JSON array from free-form model text.

    Accepts a bare array or an object holding an ``entries`` list, both
    possibly wrapped in prose. Returns None when nothing parses — never
    raises, and never guesses.
    """
    if not text:
        return None
    candidates: list[str] = []
    m = _ARRAY_RE.search(text)
    if m:
        candidates.append(m.group(0))
    o = _OBJECT_RE.search(text)
    if o:
        try:
            obj = json.loads(_salvage(o.group(0)))
            if isinstance(obj, dict) and isinstance(obj.get("entries"), list):
                candidates.append(json.dumps(obj["entries"]))
        except json.JSONDecodeError:
            pass
    for candidate in candidates:
        try:
            data = json.loads(_salvage(candidate))
            if isinstance(data, list):
                return data
        except json.JSONDecodeError:
            continue
    return None


@dataclass
class FactCheckEntry:
    """One doctrine entry's adversarial verdict. All scalars coerced."""

    id: str
    verdict: str = "unverifiable"     # confirmed | corrected | rejected | unverifiable
    source_exists: bool = False
    source_says_this: bool = False
    is_attributable: bool = False
    framing_honest: bool = False
    note: str = ""

    @property
    def passed(self) -> bool:
        return self.verdict == "confirmed"


@dataclass
class FactCheckReport:
    seat_id: str
    seat_name: str
    seat_title: str = ""
    checked_at: str = ""
    model: str = ""
    entries: list[FactCheckEntry] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        counts = {v: 0 for v in VERDICTS}
        for e in self.entries:
            counts[e.verdict] = counts.get(e.verdict, 0) + 1
        return counts

    def all_confirmed(self) -> bool:
        return bool(self.entries) and all(e.passed for e in self.entries)

    def to_markdown(self) -> str:
        lines = [
            f"# Fact-check — {self.seat_name} ({self.seat_title})",
            "",
            f"Checked {self.checked_at} by an independent adversarial pass"
            + (f" (model: {self.model})" if self.model else "")
            + ". The checker saw only the dossier and was instructed to assume it is wrong.",
            "",
            "| Entry | Verdict | Source exists | Source says it | Attributable | Framing honest | Note |",
            "|---|---|---|---|---|---|---|",
        ]
        for e in self.entries:
            lines.append(
                f"| {e.id} | {e.verdict} | {e.source_exists} | {e.source_says_this} "
                f"| {e.is_attributable} | {e.framing_honest} | {e.note} |"
            )
        counts = self.counts()
        lines += [
            "",
            f"Summary: {counts['confirmed']} confirmed, {counts['corrected']} corrected, "
            f"{counts['rejected']} rejected, {counts['unverifiable']} unverifiable.",
            "",
            "What was rejected is as informative as what survived — every rejected or "
            "corrected entry below is an entry the seats are no longer trusted to cite "
            "as documented fact.",
        ]
        return "\n".join(lines)


_VERIFIER_SYSTEM = (
    "You are an independent fact-checker hired to audit a dossier of principles "
    "attributed to a named person. The researcher who wrote it is not you, and you "
    "have not seen their reasoning. ASSUME THE FILE IS WRONG and try to refute every "
    "entry.\n"
    "For each numbered entry check four things:\n"
    "1. source_exists — does the cited source exist as described (title, format, date)?\n"
    "2. source_says_this — does that source actually say this, or is it a paraphrase or invention?\n"
    "3. is_attributable — is this THIS person's idea, or did they credit it to someone else "
    "(a framework they explicitly attributed, a ratio they publicly disowned)?\n"
    "4. framing_honest — is the framing honest? A narrow finding presented as a general law "
    "is a defect even when every word is true.\n"
    "Then give one verdict per entry:\n"
    '- "confirmed" — survived: source exists, says this, attributable, framing honest\n'
    '- "corrected" — substantially right but the framing or attribution needs fixing\n'
    '- "rejected" — cannot stand: source doesn\'t exist as described, doesn\'t say this, '
    "misattributed, or dishonestly framed\n"
    '- "unverifiable" — you cannot determine this from your knowledge; say so rather than guess\n'
    "Known traps to look for:\n"
    "- podcast episodes that do not exist as described\n"
    "- statistics that are true but narrower than presented\n"
    "- ratios the person has publicly disowned\n"
    "- famous frameworks the person explicitly credited to someone else in the very source cited\n"
    "Answer with ONLY a JSON array, no prose around it:\n"
    '[{"id": "D1", "source_exists": true, "source_says_this": true, "is_attributable": true, '
    '"framing_honest": true, "verdict": "confirmed", "note": "one sentence"}]'
)


def _dossier_for_checker(seat: Seat) -> str:
    """Render only the doctrine + sources — the checker's entire world."""
    lines = [
        f"The dossier claims the following numbered principles belong to {seat.name} "
        f"(seat: {seat.seat_title}).",
        "",
    ]
    for entry in seat.doctrine:
        status_note = " (operator-authored, not fact-checked)" if entry.status == "user" else ""
        lines += [
            f"### {entry.id} — {entry.title}{status_note}",
            entry.content,
            f"Source: {entry.source}",
            "",
        ]
    return "\n".join(lines)


def _coerce_entry(item: Any, expected_id: str) -> FactCheckEntry:
    if not isinstance(item, dict):
        return FactCheckEntry(id=expected_id, note="checker returned a non-object entry")
    eid = coerce_str(item.get("id")).upper() or expected_id
    verdict = coerce_str(item.get("verdict")).strip().lower()
    if verdict not in VERDICTS:
        note = coerce_str(item.get("note"))
        note = (note + " " if note else "") + "[verdict field malformed — treated as unverifiable]"
        return FactCheckEntry(
            id=eid,
            verdict="unverifiable",
            source_exists=coerce_bool(item.get("source_exists")),
            source_says_this=coerce_bool(item.get("source_says_this")),
            is_attributable=coerce_bool(item.get("is_attributable")),
            framing_honest=coerce_bool(item.get("framing_honest")),
            note=note.strip(),
        )
    return FactCheckEntry(
        id=eid,
        verdict=verdict,
        source_exists=coerce_bool(item.get("source_exists")),
        source_says_this=coerce_bool(item.get("source_says_this")),
        is_attributable=coerce_bool(item.get("is_attributable")),
        framing_honest=coerce_bool(item.get("framing_honest")),
        note=coerce_str(item.get("note")),
    )


async def verify_dossier(
    seat: Seat,
    llm: LLMCall,
    timeout_s: float = CHECK_TIMEOUT_S,
    max_tokens: int = CHECK_MAX_TOKENS,
) -> FactCheckReport:
    """One adversarial model call per dossier. Never raises on a bad parse —
    unparsable entries degrade to 'unverifiable', and a failed call marks the
    whole dossier unverifiable (loud, never silent)."""
    report = FactCheckReport(
        seat_id=seat.id,
        seat_name=seat.name,
        seat_title=seat.seat_title,
        checked_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
    try:
        raw = await asyncio.wait_for(
            llm(
                [
                    {"role": "system", "content": _VERIFIER_SYSTEM},
                    {"role": "user", "content": _dossier_for_checker(seat)},
                ],
                temperature=0.0,
                max_tokens=max_tokens,
            ),
            timeout=timeout_s,
        )
    except asyncio.TimeoutError:
        report.entries = [
            FactCheckEntry(id=e.id, note=f"checker timed out after {timeout_s}s — unverifiable")
            for e in seat.doctrine
        ]
        return report
    except Exception as exc:  # a failed check is loud, never silent
        log.exception("board: fact-check call failed for %s", seat.id)
        report.entries = [
            FactCheckEntry(id=e.id, note=f"checker call failed ({str(exc)[:120]}) — unverifiable")
            for e in seat.doctrine
        ]
        return report

    data = extract_json_array(raw)
    if data is None:
        report.entries = [
            FactCheckEntry(id=e.id, note="checker response did not parse as JSON — unverifiable")
            for e in seat.doctrine
        ]
        return report

    by_id = {e.id: e for e in seat.doctrine}
    seen: set[str] = set()
    for item in data:
        entry = _coerce_entry(item, "")
        if not entry.id:
            continue
        seen.add(entry.id)
        if entry.id not in by_id:
            # The checker invented an id — keep it visible, tagged as such.
            entry.note = (entry.note + " " if entry.note else "") + "[checker cited an id not in the dossier]"
        report.entries.append(entry)

    # Every doctrine entry the checker said nothing about is unverifiable,
    # never assumed fine.
    for eid, entry in by_id.items():
        if eid not in seen:
            report.entries.append(
                FactCheckEntry(id=eid, note="checker returned no verdict for this entry")
            )

    report.entries.sort(key=lambda e: e.id)
    return report


# ---------------------------------------------------------------- CLI
def _build_llm() -> Any:
    """The same LLMRouter Emma uses, from the same settings."""
    from backend.config import Settings
    from llm.router import LLMRouter

    s = Settings()
    return LLMRouter(
        ollama_url=s.ollama_url,
        groq_api_key=s.groq_api_key,
        local_model=s.local_model,
        cloud_model=s.cloud_model,
        ollama_cloud_model=s.ollama_cloud_model,
        domain=s.domain,
        num_ctx=getattr(s, "ollama_num_ctx", None),
        num_gpu=getattr(s, "ollama_num_gpu", None),
        keep_alive=getattr(s, "ollama_keep_alive", None),
    )


async def _run(llm: Any, dossiers_dir: Path, out_dir: Path, only: Optional[str]) -> list[FactCheckReport]:
    seats = load_roster(dossiers_dir)
    if only:
        seats = [s for s in seats if s.id == only]
        if not seats:
            print(f"No dossier with id '{only}' in {dossiers_dir}")
            return []
    out_dir.mkdir(parents=True, exist_ok=True)
    reports: list[FactCheckReport] = []
    for seat in seats:
        print(f"Fact-checking {seat.name} ({seat.id})...", flush=True)
        report = await verify_dossier(seat, llm.complete)
        if report.model == "" and getattr(llm, "last_served", None):
            report.model = str(llm.last_served.get("model", ""))
        path = out_dir / f"FACTCHECK_{seat.id}.md"
        path.write_text(report.to_markdown(), encoding="utf-8")
        counts = report.counts()
        print(
            f"  {seat.id}: {counts['confirmed']} confirmed, {counts['corrected']} corrected, "
            f"{counts['rejected']} rejected, {counts['unverifiable']} unverifiable -> {path.name}",
            flush=True,
        )
        reports.append(report)
    return reports


async def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Adversarial fact-check of board dossiers")
    parser.add_argument("--seat", help="only check this seat id")
    parser.add_argument(
        "--out",
        default=str(Path(__file__).resolve().parent),
        help="directory for FACTCHECK_<seat>.md reports",
    )
    args = parser.parse_args(argv)

    dossiers_dir = Path(__file__).resolve().parent.parent / "dossiers"
    llm = _build_llm()
    reports = await _run(llm, dossiers_dir, Path(args.out), args.seat)
    if reports and all(r.all_confirmed() for r in reports):
        print("\nAll dossiers fully confirmed by the adversarial pass.")
    else:
        print("\nReview the per-seat reports - entries marked corrected or rejected")
        print("are not trusted as documented fact until the dossier is fixed.")
    return 0 if reports else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
