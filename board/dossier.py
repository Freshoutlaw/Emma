"""Dossier parser (Tier 1) — one markdown file → one seat, or nothing.

Rejects, with a logged reason:
- a file with no domains (could never be routed to)
- a file with no doctrine (there is nothing to cite)
- a file with duplicate doctrine ids (a citation to D3 when two entries
  claim D3 is ambiguous — anti-fabrication machinery that fails open is
  not machinery)

Reads UTF-8 tolerantly: a wrong-encoding file degrades to a logged warning
rather than an exception that takes out the roster. A malformed dossier
loses *its own seat* and logs why — a meeting never fails because one file
was hand-edited badly, and a seat never vanishes silently.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

from board.models import DoctrineEntry, Seat

logger = logging.getLogger(__name__)

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_DOCTRINE_HEADER_RE = re.compile(r"^#{3,4}\s+(D\d+)\s*(?:[—–-]\s*)?(.*)$", re.IGNORECASE)
_BULLET_PREFIX_RE = re.compile(r"^[\s*\-]*")
_SECTION_RE = re.compile(r"^##\s+(.+)$")


class DossierError(ValueError):
    """A dossier failed validation — its seat is dropped, the roster survives."""


def _tolerant_read(path: Path) -> Optional[str]:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        # Wrong encoding: degrade with a warning, never raise into the roster.
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
            logger.warning("dossier %s: not valid UTF-8 — read with replacement chars", path.name)
            return text
        except OSError as exc:
            logger.warning("dossier %s: unreadable (%s)", path.name, exc)
            return None
    except OSError as exc:
        logger.warning("dossier %s: unreadable (%s)", path.name, exc)
        return None


def _parse_frontmatter(block: str) -> dict[str, str]:
    data: dict[str, str] = {}
    for line in block.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        data[key.strip().lower()] = value.strip()
    return data


def _parse_list(value: str) -> list[str]:
    return [item.strip() for item in re.split(r"[,\[\]\n]+", value) if item.strip()]


def parse_dossier(path: Path, log_silent: bool = False) -> Optional[Seat]:
    """Parse one dossier file. Returns a Seat, or None (logged reason)."""
    name = path.name
    text = _tolerant_read(path)
    if text is None:
        return None

    m = _FRONTMATTER_RE.match(text)
    if not m:
        if not log_silent:
            logger.warning("dossier %s: no frontmatter — rejected", name)
        return None
    meta = _parse_frontmatter(m.group(1))
    rest = text[m.end():]

    seat_id = meta.get("id")
    seat_name = meta.get("name")
    if not seat_id or not seat_name:
        if not log_silent:
            logger.warning("dossier %s: missing id/name in frontmatter — rejected", name)
        return None

    domains = _parse_list(meta.get("domains", ""))
    if not domains:
        if not log_silent:
            logger.warning("dossier %s: no domains — could never be routed, rejected", name)
        return None

    # Split into sections.
    sections: dict[str, list[str]] = {}
    current: Optional[str] = None
    current_lines: list[str] = []
    for line in rest.splitlines():
        sec = _SECTION_RE.match(line)
        if sec:
            if current:
                sections.setdefault(current, []).extend(current_lines)
            current = sec.group(1).strip().lower()
            current_lines = []
        else:
            current_lines.append(line)
    if current:
        sections.setdefault(current, []).extend(current_lines)

    # ---- doctrine -----------------------------------------------------
    doctrine: list[DoctrineEntry] = []
    seen_ids: set[str] = set()
    doctrine_lines = sections.get("doctrine", [])
    current_entry: Optional[dict] = None

    def flush_entry() -> None:
        nonlocal current_entry
        if current_entry is None:
            return
        eid = current_entry["id"]
        if eid in seen_ids:
            raise DossierError(
                f"duplicate doctrine id {eid} in {name} — ambiguous citations, rejected"
            )
        title = current_entry["title"]
        source = current_entry.get("source", "").strip()
        content = "\n".join(current_entry["body"]).strip()
        status = current_entry.get("status", "").strip().lower()
        if not source:
            raise DossierError(f"doctrine {eid} in {name}: no Source — nothing checkable, rejected")
        if not content:
            raise DossierError(f"doctrine {eid} in {name}: empty body, rejected")
        if status not in ("sourced", "user"):
            status = "sourced" if status == "sourced" else "user"
        seen_ids.add(eid)
        doctrine.append(DoctrineEntry(
            id=eid,
            title=title,
            content=content,
            source=source,
            status=status,
        ))
        current_entry = None

    for line in doctrine_lines:
        hdr = _DOCTRINE_HEADER_RE.match(line)
        if hdr:
            flush_entry()
            current_entry = {
                "id": hdr.group(1).upper(),
                "title": hdr.group(2).strip(),
                "body": [],
                "source": "",
                "status": "",
            }
            continue
        if current_entry is None:
            continue
        stripped = line.strip().lstrip("-*• ").strip()
        low = stripped.lower()
        if low.startswith("source:"):
            current_entry["source"] = stripped.split(":", 1)[1].strip()
        elif low.startswith("status:"):
            current_entry["status"] = stripped.split(":", 1)[1].strip()
        else:
            current_entry["body"].append(line)
    flush_entry()

    if not doctrine:
        raise DossierError(f"{name}: no doctrine — nothing to cite, rejected")

    def section_text(key: str) -> str:
        return "\n".join(sections.get(key, [])).strip()

    blind_spots = [
        ln.strip().lstrip("-*•").strip()
        for ln in sections.get("blind spots", [])
        if ln.strip()
    ]
    seat = Seat(
        id=seat_id,
        name=seat_name,
        seat_title=meta.get("seat_title", ""),
        domains=domains,
        status=meta.get("status", "active"),
        characteristic_objection=section_text("characteristic objection"),
        blind_spots=blind_spots,
        voice=section_text("voice"),
        doctrine=doctrine,
        source_file=str(path),
    )
    return seat


def load_roster(dossier_dir: Path) -> list[Seat]:
    """Load every dossier in a directory. Malformed files lose their own
    seat and log why — a quorum that quietly shrinks is worse than a stale
    one, so failures are always loud in the log."""
    roster: list[Seat] = []
    for path in sorted(dossier_dir.glob("*.md")):
        try:
            seat = parse_dossier(path)
            if seat is not None:
                roster.append(seat)
        except DossierError as exc:
            logger.warning("dossier %s rejected: %s", path.name, exc)
        except Exception as exc:  # never take the roster down
            logger.exception("dossier %s crashed the parser: %s", path.name, exc)
    return roster
