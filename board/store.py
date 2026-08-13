"""Store (Tier 6) — meetings, citation snapshots, and the edit rules.

- Every meeting is stored: question, seats, opinions, verdict, cost.
- When a meeting is stored, each cited entry's title and source are
  SNAPSHOTTED into the opinion, so a stored meeting renders its own
  citations without re-reading the dossier and stays readable after the
  dossier changes.
- Seats are editable, with the two learned rules enforced here:

  * Retire, never delete. Retiring a doctrine id keeps it spoken for
    forever — a later entry can never inherit D3 and silently re-point
    every stored citation at different content.
  * Never edit a verification state. An edit that changes an entry's
    substance drops its status to ``user`` automatically, because the
    fact-check no longer covers what it now says.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from board.models import DoctrineEntry, MeetingResult, Seat

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meetings (
    id            TEXT PRIMARY KEY,
    ts            TEXT NOT NULL,
    question      TEXT NOT NULL,
    prompted      INTEGER NOT NULL DEFAULT 1,
    brief         TEXT,
    verdict       TEXT,
    cost_usd      REAL NOT NULL DEFAULT 0,
    status        TEXT NOT NULL DEFAULT 'complete'
);
CREATE TABLE IF NOT EXISTS opinions (
    meeting_id    TEXT NOT NULL,
    seat_id       TEXT NOT NULL,
    seat_name     TEXT NOT NULL,
    position      TEXT,
    reasoning     TEXT,
    citations     TEXT,           -- JSON list of valid ids
    citation_sources TEXT,        -- JSON {id: {title, source}} snapshot
    confidence    REAL NOT NULL DEFAULT 0,
    would_change_mind TEXT,
    abstain       INTEGER NOT NULL DEFAULT 0,
    unsourced     INTEGER NOT NULL DEFAULT 0,
    error         TEXT,
    PRIMARY KEY (meeting_id, seat_id)
);
CREATE TABLE IF NOT EXISTS edits (
    seat_id       TEXT NOT NULL,
    doctrine_id   TEXT NOT NULL,
    action        TEXT NOT NULL,   -- 'add' | 'edit' | 'retire'
    content       TEXT,
    source        TEXT,
    ts            TEXT NOT NULL,
    PRIMARY KEY (seat_id, doctrine_id, action)
);
"""


class BoardStore:
    def __init__(self, db_path: str | Path = "data/board.db") -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    # ------------------------------------------------------------ meetings
    def save_meeting(
        self,
        meeting: MeetingResult,
        citation_snapshots: Optional[dict[str, dict[str, dict]]] = None,
    ) -> None:
        """Store a meeting with per-opinion citation snapshots."""
        citation_snapshots = citation_snapshots or {}
        verdict = None if meeting.verdict is None else meeting.verdict.__dict__
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO meetings (id, ts, question, prompted, brief, verdict, cost_usd, status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    meeting.meeting_id, meeting.ts, meeting.question,
                    int(meeting.prompted), meeting.brief,
                    json.dumps(verdict) if verdict else None,
                    float(meeting.cost_usd),
                    "declined" if meeting.declined else ("error" if meeting.error else "complete"),
                ),
            )
            for opinion in meeting.opinions:
                snap = citation_snapshots.get(opinion.seat_id, {})
                conn.execute(
                    "INSERT OR REPLACE INTO opinions (meeting_id, seat_id, seat_name, position, reasoning,"
                    " citations, citation_sources, confidence, would_change_mind, abstain, unsourced, error) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        meeting.meeting_id, opinion.seat_id, opinion.seat_name,
                        opinion.position, opinion.reasoning,
                        json.dumps(opinion.citations),
                        json.dumps(snap),
                        float(opinion.confidence),
                        opinion.would_change_mind,
                        int(opinion.abstain), int(opinion.unsourced),
                        opinion.error,
                    ),
                )

    def latest_meeting(self) -> Optional[dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM meetings ORDER BY ts DESC LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            data = dict(row)
            opinions = conn.execute(
                "SELECT * FROM opinions WHERE meeting_id = ? ORDER BY seat_name",
                (data["id"],),
            ).fetchall()
            data["opinions"] = [dict(o) for o in opinions]
            return data

    def last_standing_review(self) -> Optional[str]:
        """ts of the most recent unprompted standing review, or None."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT ts FROM meetings WHERE prompted = 0 AND status = 'complete' "
                "ORDER BY ts DESC LIMIT 1"
            ).fetchone()
            return None if row is None else row["ts"]

    def count_meetings(self) -> int:
        with self._connect() as conn:
            return conn.execute("SELECT COUNT(*) FROM meetings").fetchone()[0]

    # ------------------------------------------------------------ edits
    def apply_edit(
        self,
        seat_id: str,
        doctrine_id: str,
        action: str,
        content: Optional[str] = None,
        source: Optional[str] = None,
    ) -> str:
        """Apply an operator edit under the two rules.

        Returns a human-readable result. Raises ValueError on an illegal
        action (e.g. trying to set a verification state).
        """
        doctrine_id = doctrine_id.strip().upper()
        if action not in ("add", "edit", "retire"):
            raise ValueError(f"unknown action '{action}'")
        if action in ("add", "edit") and not (content or "").strip():
            raise ValueError("content required for add/edit")
        if self.is_retired(seat_id, doctrine_id):
            raise ValueError(f"{doctrine_id} is retired — ids are never reused")
        ts = datetime.now(timezone.utc).isoformat()
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO edits (seat_id, doctrine_id, action, content, source, ts) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (seat_id, doctrine_id, action, content, source, ts),
            )
        if action == "retire":
            return f"retired {doctrine_id} from {seat_id} — id is spoken for forever"
        # A substance edit drops the entry to 'user': the fact-check no
        # longer covers what it now says. The verification state itself is
        # never editable.
        return f"{action} {doctrine_id} in {seat_id} — status now 'user' (operator-authored)"

    def is_retired(self, seat_id: str, doctrine_id: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM edits WHERE seat_id = ? AND doctrine_id = ? AND action = 'retire'",
                (seat_id, doctrine_id),
            ).fetchone()
            return row is not None

    def edits_for(self, seat_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM edits WHERE seat_id = ? ORDER BY ts", (seat_id,)
            ).fetchall()
            return [dict(r) for r in rows]

    def compose_seat(self, seat: Seat) -> Seat:
        """The seat as the board will see it: file doctrine, minus retired
        ids, plus user-added entries (status 'user'). Never mutates the file.

        The citation gate's valid set is THIS composed view — what the seat
        was actually shown — not the raw file.
        """
        file_entries = {e.id: e for e in seat.doctrine}
        edits = self.edits_for(seat.id)
        composed: list[DoctrineEntry] = []
        retired: set[str] = set()
        for edit in edits:
            eid = edit["doctrine_id"]
            if edit["action"] == "retire":
                retired.add(eid)
            elif edit["action"] == "edit" and eid in file_entries:
                original = file_entries[eid]
                composed.append(DoctrineEntry(
                    id=eid,
                    title=original.title,
                    content=edit["content"] or original.content,
                    source=edit["source"] or original.source,
                    status="user",  # substance changed -> fact-check no longer covers it
                ))
        for entry in seat.doctrine:
            if entry.id in retired or entry.id in {e.id for e in composed}:
                continue
            composed.append(entry)
        for edit in edits:
            if edit["action"] == "add" and edit["doctrine_id"] not in retired:
                composed.append(DoctrineEntry(
                    id=edit["doctrine_id"],
                    title=f"Operator addition {edit['doctrine_id']}",
                    content=edit["content"] or "",
                    source=edit["source"] or "operator",
                    status="user",
                ))
        seen: set[str] = set()
        final: list[DoctrineEntry] = []
        for entry in composed:
            if entry.id in seen:
                continue  # a duplicate add is collapsed; file dups are rejected by the parser
            seen.add(entry.id)
            final.append(entry)
        return Seat(
            id=seat.id,
            name=seat.name,
            seat_title=seat.seat_title,
            domains=seat.domains,
            status=seat.status,
            characteristic_objection=seat.characteristic_objection,
            blind_spots=seat.blind_spots,
            voice=seat.voice,
            doctrine=final,
            source_file=seat.source_file,
        )
