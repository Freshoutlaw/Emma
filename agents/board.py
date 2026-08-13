"""Board of Advisors agent — 'ask the board about X'.

This is the single tool that convenes the council: triage (decline gate),
seat selection, the isolated fan-out, the chair, storage with citation
snapshots, and the HUD board panel.

Spoken convene phrase:  "ask the board about <question>"
Administrative phrases: "board status" / "board list" / "board retire <seat> <D#>"
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from agents.base import AgentResult, BaseAgent
from board.brief import build_brief
from board.chair import ChairRunner
from board.dossier import load_roster
from board.meeting import MeetingRunner
from board.router import BoardRouter, normalize_text
from board.store import BoardStore

log = logging.getLogger(__name__)

DOSSIERS_DIR = Path(__file__).resolve().parent.parent / "board" / "dossiers"


class BoardAgent(BaseAgent):
    name = "board"
    description = "Convenes the board of advisors for a real business decision ('ask the board about X')."

    def __init__(self, pipeline: Any) -> None:
        super().__init__(pipeline)
        self.store = BoardStore(pipeline.settings.data_dir / "board.db")
        ceiling = float(getattr(pipeline.settings, "board_cost_ceiling_usd", 0.50))
        max_seats = int(getattr(pipeline.settings, "board_max_seats", 4))
        self.runner = MeetingRunner(
            llm=pipeline.llm.complete,
            usage_repo=pipeline.usage_repo,
            cost_ceiling_usd=ceiling,
            max_seats=max_seats,
        )
        self.chair = ChairRunner(pipeline.llm.complete)
        self.router = BoardRouter(llm=pipeline.llm.complete, max_seats=max_seats)

    # ------------------------------------------------------------ roster
    def _roster(self) -> list:
        return load_roster(DOSSIERS_DIR)

    # ------------------------------------------------------------ convene
    async def convene(self, question: str, prompted: bool = True) -> AgentResult:
        roster = self._roster()
        brief = await build_brief(self.pipeline)
        triage = await self.router.triage(question, roster)

        if triage.declined:
            return AgentResult(
                ok=True,
                output=(
                    f"The board declined this question: {triage.decline_reason}. "
                    "Business decisions about your own company are the board's core "
                    "use case; code review, medical, legal, and family matters are not."
                ),
                intent="board",
            )

        seats = [self.store.compose_seat(s) for s in triage.seats]
        if not seats:
            return AgentResult(
                ok=True,
                output=(
                    "No advisor has standing on this question, and none was named. "
                    "Name an advisor (Hormozi, McKenzie, Fried, Thompson) or ask about "
                    "offers, pricing, strategy, operations, or growth."
                ),
                intent="board",
            )

        meeting = await self.runner.run(
            question, seats, brief.full, brief.short, prompted=prompted,
        )
        if meeting.error and not meeting.opinions:
            return AgentResult(ok=False, output=f"The board could not convene: {meeting.error}", intent="board", error=meeting.error)

        if meeting.opinions:
            verdict = await self.chair.run(question, seats, meeting.opinions, brief.full)
            meeting.verdict = verdict

        # Snapshot cited entries' titles/sources at meeting time.
        snapshots: dict[str, dict[str, dict]] = {}
        for seat in seats:
            by_id = {e.id: e for e in seat.doctrine}
            for opinion in meeting.opinions:
                if opinion.seat_id != seat.id:
                    continue
                snapshots.setdefault(seat.id, {})
                for cid in opinion.citations:
                    entry = by_id.get(cid)
                    if entry is not None:
                        snapshots[seat.id][cid] = {"title": entry.title, "source": entry.source}
        self.store.save_meeting(meeting, snapshots)

        payload = meeting.to_payload(snapshots)
        try:
            self.pipeline.display.set("board", reason="meeting complete", payload=payload)
        except Exception:
            pass

        spoken = (meeting.verdict.spoken_summary if meeting.verdict else None) or "The board met, but produced no spoken summary."
        output = (
            f"Board meeting {meeting.meeting_id} complete. {spoken} "
            f"Details are on the board panel. Cost: ${meeting.cost_usd:.4f}."
        )
        return AgentResult(
            ok=True,
            output=output,
            intent="board",
            actions=[{"tool": "board", "args": {"meeting_id": meeting.meeting_id}}],
        )

    # ------------------------------------------------------------ run
    async def run(self, request: str) -> AgentResult:
        text = request.strip()
        low = normalize_text(text)

        if low in ("board status", "board status?", "status of the board"):
            latest = self.store.latest_meeting()
            if latest is None:
                return AgentResult(ok=True, output="No board meetings yet.", intent="board")
            opinions = latest.get("opinions") or []
            lines = [f"Last meeting {latest['id']} at {latest['ts']}: {latest['question']}"]
            for o in opinions:
                lines.append(
                    f"  {o['seat_name']}: {o['position'] or '(no position)'}"
                    + (" [abstained]" if o["abstain"] else "")
                    + (" [no citations]" if o["unsourced"] else "")
                )
            return AgentResult(ok=True, output="\n".join(lines), intent="board")

        if low in ("board list", "board roster", "who is on the board"):
            roster = self._roster()
            lines = [f"{s.name} — {s.seat_title} (domains: {', '.join(s.domains)})" for s in roster]
            return AgentResult(ok=True, output="The board:\n" + "\n".join(lines), intent="board")

        retire = re.match(r"^board\s+retire\s+(\w+)\s+(D\d+)\s*$", text, re.IGNORECASE)
        if retire:
            try:
                msg = self.store.apply_edit(retire.group(1).lower(), retire.group(2).upper(), "retire")
                return AgentResult(ok=True, output=msg, intent="board")
            except ValueError as exc:
                return AgentResult(ok=False, output=str(exc), intent="board", error=str(exc))

        # "ask the board about X" / "ask the board X" / "board: X"
        m = re.match(
            r"^(?:ask\s+(?:the\s+)?board\s+(?:about\s+|to\s+consider\s+|on\s+)?|"
            r"convene\s+(?:the\s+)?board\s+(?:on\s+|for\s+)?|"
            r"what\s+would\s+(?:the\s+)?board\s+(?:think|say)\s+(?:about\s+)?|"
            r"board\s*:\s*)(.+)$",
            text,
            re.IGNORECASE | re.DOTALL,
        )
        if m and m.group(1).strip():
            return await self.convene(m.group(1).strip(), prompted=True)

        # Seat names alone also convene: "ask hormozi and thompson about X"
        seats = self._roster()
        surnames = [s for seat in seats for s in (seat.name.split()[1:] or [seat.name])]
        if any(surn.lower() in low for surn in surnames):
            return await self.convene(text, prompted=True)

        return AgentResult(
            ok=False,
            output=(
                'Say "ask the board about <question>" to convene the board, '
                '"board status" for the last meeting, "board list" for the roster, '
                'or "board retire <seat> <D#>" to retire a doctrine entry.'
            ),
            intent="board",
            error="board phrasing not recognized",
        )
