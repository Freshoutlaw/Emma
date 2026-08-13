"""The citation gate (Tier 1) — a pure function, deliberately boring.

Given whatever the model returned as citations and the set of doctrine ids
that seat was *actually shown*, return only the valid ones. Uppercase-
normalize, de-duplicate, preserve order, discard everything else.

The valid set is what the seat was shown — NOT everything in the file.
Retired entries (Tier 6) and user-added entries diverge from the raw file,
and the gap is exactly where a seat would cite something withheld from it.
"""

from __future__ import annotations

import re
from typing import Iterable, Optional


def normalize_citation(raw: str) -> Optional[str]:
    """Normalize a single citation: ' d3 ' / 'D3' / 'd.3' / 'd-3' → 'D3'.

    Returns None for anything that is not a well-formed doctrine id
    (e.g. a URL, a sentence, an empty string) — such values are discarded
    rather than guessed at.
    """
    if not raw or not isinstance(raw, str):
        return None
    cleaned = raw.strip()
    # allow "D3", "D 3", "D-3", "D.3" — never free text
    m = re.fullmatch(r"[Dd]\s*[-._]?\s*(\d+)", cleaned)
    if not m:
        return None
    return f"D{int(m.group(1))}"


def filter_citations(
    cited: Iterable,
    shown_ids: Iterable[str],
) -> tuple[list[str], list[str]]:
    """Return (valid, rejected) citation ids.

    - valid: normalized, de-duplicated, in original order, and present in
      ``shown_ids`` (the set the seat was actually shown).
    - rejected: everything else — fabricated ids, free text, repeats of
      ids that were never shown.
    """
    valid_set = set(shown_ids)
    seen: set[str] = set()
    valid: list[str] = []
    rejected: list[str] = []
    for raw in cited:
        norm = normalize_citation(raw)
        if norm is None:
            rejected.append(str(raw))
            continue
        if norm not in valid_set:
            rejected.append(norm)
            continue
        if norm in seen:
            continue  # de-duplicate silently (not "rejected" — it was valid)
        seen.add(norm)
        valid.append(norm)
    return valid, rejected


def is_unsourced(citations: list[str], abstain: bool) -> bool:
    """A seat that cites nothing and didn't abstain is *unsourced* — it
    spoke without support. Not an error; a flag the operator should see."""
    return not abstain and not citations
