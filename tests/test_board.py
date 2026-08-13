"""Board of Advisors — guard tests for every tier.

Covers:
- Tier 1: parser rejection (no domains / no doctrine / duplicate ids),
  encoding tolerance, citation gate (normalize, de-dupe, order, strip,
  unsourced flag)
- Tier 2: adversarial fact-check — hostile scalar coercion ("false" string),
  prose-wrapped JSON, missing entries degrade to unverifiable, malformed
  verdicts, failed calls are loud not silent
- Tier 3: unicode name normalization, surname-required matching, the
  decline gate incl. "cut this product loose" never declined
- Tier 4: zero ceiling = zero calls, de-dupe, timeout tolerance, citation
  stripping from seat output
- Tier 5: unanimity guard (1 voice is never unanimous), spoken-summary guard
- Tier 6: retirement blocks id reuse, substance edits drop to 'user',
  citation snapshots stored with meetings
"""

from __future__ import annotations

import asyncio
import json
import types
from pathlib import Path

import pytest

from board.brief import build_brief
from board.citations import filter_citations, is_unsourced
from board.chair import ChairRunner, can_be_unanimous, compute_verdict_counts, spoken_summary_matches
from board.dossier import DossierError, load_roster, parse_dossier
from board.meeting import MeetingRunner, _parse_seat_opinion
from board.metrics import BusinessMetrics, extract_from_memory, probe_business_sql
from board.models import ChairVerdict, DoctrineEntry, Seat, SeatOpinion
from board.research.verify import extract_json_array, verify_dossier
from board.router import BoardRouter, normalize_text
from board.store import BoardStore

DOSSIERS = Path(__file__).resolve().parent.parent / "board" / "dossiers"

GOOD_DOSSIER = """---
id: testseat
name: Test Operator
seat_title: Test Seat
domains: pricing, strategy
status: active
---

## Characteristic objection
Pushes back on anything vague.

## Blind spots
- Does not transfer to consumer products.

## Voice
Dry and precise.

## Doctrine

### D1 — First principle
The first principle.
- Source: Some Book (Someone, 2020)
- Status: sourced

### D2 — Second principle
The second principle.
- Source: Some Essay (Someone, 2021)
- Status: sourced
"""


# ================================================================== Tier 1
def test_parse_valid_dossier(tmp_path):
    seat = parse_dossier(_write(GOOD_DOSSIER, tmp_path))
    assert seat is not None
    assert seat.id == "testseat"
    assert seat.doctrine_ids() == {"D1", "D2"}


def test_parse_rejects_missing_domains(tmp_path):
    text = GOOD_DOSSIER.replace("domains: pricing, strategy", "domains:")
    assert parse_dossier(_write(text, tmp_path)) is None


def test_parse_rejects_missing_doctrine(tmp_path):
    text = GOOD_DOSSIER.split("## Doctrine")[0]
    with pytest.raises(DossierError):
        parse_dossier(_write(text, tmp_path))


def test_parse_rejects_duplicate_doctrine_ids(tmp_path):
    dup = GOOD_DOSSIER.replace("### D2 — Second principle", "### D1 — Second principle")
    with pytest.raises(DossierError):
        parse_dossier(_write(dup, tmp_path))


def test_parse_tolerates_bad_encoding(tmp_path):
    path = tmp_path / "latin.md"
    path.write_bytes(GOOD_DOSSIER.encode("latin-1", errors="replace"))
    # Must degrade with a logged warning, not raise.
    seat = parse_dossier(path)
    assert seat is None or seat.id == "testseat"


def test_real_roster_loads():
    roster = load_roster(DOSSIERS)
    assert len(roster) == 4
    for seat in roster:
        assert seat.doctrine_ids(), f"{seat.id} has no doctrine"
        assert len({e.id for e in seat.doctrine}) == len(seat.doctrine)


# ================================================================== gate
def test_gate_normalizes_dedupes_preserves_order():
    valid, rejected = filter_citations([" d3 ", "D3", "d-2", "D1", "D3", "D2"], {"D1", "D2", "D3"})
    assert valid == ["D3", "D2", "D1"]
    assert rejected == []


def test_gate_strips_fabricated_and_free_text():
    valid, rejected = filter_citations(["D9", "Hormozi says", "https://x.com/y", "D1"], {"D1", "D2"})
    assert valid == ["D1"]
    assert rejected == ["D9", "Hormozi says", "https://x.com/y"]


def test_gate_uses_shown_set_not_file():
    # D2 was retired — the seat was only shown D1; citing D2 must be stripped.
    valid, _ = filter_citations(["D1", "D2"], {"D1"})
    assert valid == ["D1"]


def test_unsourced_flag():
    assert is_unsourced([], False) is True
    assert is_unsourced([], True) is False
    assert is_unsourced(["D1"], False) is False


# ================================================================== Tier 2
async def _verify(llm, entries=None):
    seat = _seat(entries=entries or [
        DoctrineEntry(id="D1", title="t1", content="c1", source="s1"),
        DoctrineEntry(id="D2", title="t2", content="c2", source="s2"),
    ])
    return await verify_dossier(seat, llm)


def test_verify_coerces_hostile_scalars():
    # "false" as a string must be False, never truthy — identity, not truthiness.
    async def llm(messages, **kw):
        return ('[{"id": "D1", "source_exists": "false", "source_says_this": true, '
                '"is_attributable": "true", "framing_honest": false, '
                '"verdict": "rejected", "note": "source is a blog that does not exist"}]')
    report = asyncio.run(_verify(llm))
    e = report.entries[0]
    assert e.id == "D1"
    assert e.source_exists is False
    assert e.source_says_this is True
    assert e.is_attributable is True
    assert e.framing_honest is False
    assert e.verdict == "rejected"
    assert report.counts()["rejected"] == 1


def test_verify_extracts_array_from_prose():
    async def llm(messages, **kw):
        return ('Here is my audit. I found problems with D1:\n'
                '[{"id": "D1", "source_exists": true, "source_says_this": false, '
                '"is_attributable": true, "framing_honest": true, '
                '"verdict": "corrected", "note": "the chapter says the opposite"}]\n'
                'Hope that helps.')
    report = asyncio.run(_verify(llm))
    assert report.entries[0].verdict == "corrected"
    assert report.entries[0].source_says_this is False


def test_verify_accepts_object_with_entries_key():
    async def llm(messages, **kw):
        return ('{"entries": [{"id": "D1", "source_exists": true, "source_says_this": true, '
                '"is_attributable": true, "framing_honest": true, "verdict": "confirmed", "note": "ok"}]}')
    report = asyncio.run(_verify(llm))
    assert report.entries[0].verdict == "confirmed"


def test_verify_missing_entry_is_unverifiable_not_assumed_fine():
    async def llm(messages, **kw):
        # Checker only returned D2 — D1 must degrade loudly, never pass silently.
        return ('[{"id": "D2", "source_exists": true, "source_says_this": true, '
                '"is_attributable": true, "framing_honest": true, "verdict": "confirmed", "note": ""}]')
    report = asyncio.run(_verify(llm))
    by_id = {e.id: e for e in report.entries}
    assert by_id["D1"].verdict == "unverifiable"
    assert "no verdict" in by_id["D1"].note
    assert by_id["D2"].verdict == "confirmed"
    assert report.all_confirmed() is False


def test_verify_malformed_verdict_defaults_unverifiable():
    async def llm(messages, **kw):
        return ('[{"id": "D1", "source_exists": true, "source_says_this": true, '
                '"is_attributable": true, "framing_honest": true, "verdict": "definitely true", "note": ""}]')
    report = asyncio.run(_verify(llm))
    e = report.entries[0]
    assert e.verdict == "unverifiable"
    assert "malformed" in e.note


def test_verify_invented_checker_id_is_tagged():
    async def llm(messages, **kw):
        return ('[{"id": "D99", "source_exists": true, "source_says_this": true, '
                '"is_attributable": true, "framing_honest": true, "verdict": "confirmed", "note": ""}]')
    report = asyncio.run(_verify(llm))
    d99 = next(e for e in report.entries if e.id == "D99")
    assert "not in the dossier" in d99.note
    assert report.counts()["unverifiable"] == 2  # D1 + D2 both unanswered


def test_verify_failed_call_is_loud_never_silent():
    async def llm(messages, **kw):
        raise RuntimeError("provider down")
    report = asyncio.run(_verify(llm))
    assert len(report.entries) == 2
    assert all(e.verdict == "unverifiable" for e in report.entries)
    assert all("failed" in e.note for e in report.entries)


def test_verify_extract_json_array_garbage():
    assert extract_json_array("") is None
    assert extract_json_array("no json here") is None
    assert extract_json_array("x [1, 2] y") == [1, 2]


# ================================================================== live brief (metrics)
class _FakeEpisodic:
    def __init__(self, episodes=None):
        self.episodes = episodes or []

    def recent(self, limit=20):
        return self.episodes[:limit]

    def count(self):
        return len(self.episodes)


class _FakeQueryAgent:
    def __init__(self, configured=True, tables=None, rows=None):
        self._configured = configured
        self.tables = tables or []
        self.rows = rows or []

    def is_configured(self):
        return self._configured

    async def list_tables(self):
        return types.SimpleNamespace(
            ok=True,
            output=json.dumps([{"name": t, "type": "BASE TABLE"} for t in self.tables]),
        )

    async def query(self, sql):
        return types.SimpleNamespace(ok=True, output=json.dumps(self.rows))


def _pipeline(tmp_path, llm=None, episodes=None, query_agent=None, dsn=None):
    return types.SimpleNamespace(
        settings=types.SimpleNamespace(data_dir=tmp_path, supabase_query_dsn=dsn),
        usage_repo=_FakeRepo(),
        episodic=_FakeEpisodic(episodes),
        llm=types.SimpleNamespace(complete=llm or _FakeLLM(responses=["[]"]).complete),
        supabase_query_agent=query_agent,
    )


def _episode(eid, content, kind="user", ts="2026-08-13T10:00:00+00:00"):
    return {"id": eid, "ts": ts, "kind": kind, "content": content}


def test_metrics_store_snapshot_latest(tmp_path):
    store = BusinessMetrics(tmp_path / "business.db")
    store.record("mrr", 4000, "usd", "episode:a", "2026-08-01T00:00:00+00:00")
    store.record("mrr", 4200, "usd", "episode:b", "2026-08-13T00:00:00+00:00")
    snap = store.snapshot()
    assert len(snap) == 1
    assert snap[0]["metric"] == "mrr"
    assert snap[0]["value"] == 4200
    assert snap[0]["source"] == "episode:b"


def test_metrics_store_drops_unknown_metric(tmp_path):
    store = BusinessMetrics(tmp_path / "business.db")
    store.record("vibes", 9, "count", "episode:a", "2026-08-01T00:00:00+00:00")
    assert store.has_data() is False


def test_extract_records_with_provenance(tmp_path):
    llm = _FakeLLM(responses=['[{"metric": "mrr", "value": 4200, "unit": "usd", "episode_id": "e1", "note": "told in chat"}]'])
    pipeline = _pipeline(tmp_path, llm=llm.complete, episodes=[_episode("e1", "our MRR is $4,200")])
    recorded = asyncio.run(extract_from_memory(pipeline))
    assert recorded == 1
    snap = BusinessMetrics(tmp_path / "business.db").snapshot()
    assert snap[0]["metric"] == "mrr"
    assert snap[0]["value"] == 4200
    assert snap[0]["unit"] == "usd"
    assert snap[0]["source"] == "episode:e1"


def test_extract_rejects_fabricated_episode_id(tmp_path):
    # The extraction may not invent provenance: an id not shown is dropped.
    llm = _FakeLLM(responses=['[{"metric": "mrr", "value": 9999, "unit": "usd", "episode_id": "zzz", "note": ""}]'])
    pipeline = _pipeline(tmp_path, llm=llm.complete, episodes=[_episode("e1", "our MRR is $4,200")])
    recorded = asyncio.run(extract_from_memory(pipeline))
    assert recorded == 0
    assert BusinessMetrics(tmp_path / "business.db").has_data() is False


def test_extract_skips_when_no_new_episodes(tmp_path):
    episodes = [_episode("e1", "our MRR is $4,200")]
    llm = _FakeLLM(responses=['[{"metric": "mrr", "value": 4200, "unit": "usd", "episode_id": "e1", "note": ""}]'])
    pipeline = _pipeline(tmp_path, llm=llm.complete, episodes=episodes)
    assert asyncio.run(extract_from_memory(pipeline)) == 1
    calls_after_first = llm.calls
    # Same episodes, no new ones — the second extraction must make NO call.
    assert asyncio.run(extract_from_memory(pipeline)) == 0
    assert llm.calls == calls_after_first


def test_brief_shows_reported_figures(tmp_path):
    store = BusinessMetrics(tmp_path / "business.db")
    store.record("mrr", 4200, "usd", "episode:e1", "2026-08-13T10:00:00+00:00", "told in chat")
    store.record("subscribers", 37, "count", "episode:e2", "2026-08-13T10:00:00+00:00")
    brief = asyncio.run(build_brief(_pipeline(tmp_path)))
    assert "as reported to Emma" in brief.full
    assert "$4,200.00" in brief.full
    assert "37" in brief.full
    assert "Reported business figures" in brief.short


def test_brief_unavailable_is_actionable_not_invented(tmp_path):
    brief = asyncio.run(build_brief(_pipeline(tmp_path)))
    assert "none available yet" in brief.full
    assert "DO NOT invent" in brief.full
    assert "EMMA_SUPABASE_QUERY_DSN" in brief.full
    assert "mrr = " not in brief.full.lower()  # no invented metric line
    assert "revenue = " not in brief.full.lower()


def test_brief_shows_live_sql_probe_rows(tmp_path):
    agent = _FakeQueryAgent(configured=True, tables=["revenue", "users"], rows=[{"id": 1, "amount": 4200}])
    brief = asyncio.run(build_brief(_pipeline(tmp_path, query_agent=agent, dsn="postgres://x")))
    assert "live SQL" in brief.full
    assert "revenue" in brief.full
    assert "4200" in brief.full


def test_brief_connected_but_no_metric_tables(tmp_path):
    agent = _FakeQueryAgent(configured=True, tables=["notes", "logs"])
    brief = asyncio.run(build_brief(_pipeline(tmp_path, query_agent=agent, dsn="postgres://x")))
    assert "CONNECTED" in brief.full
    assert "no tables" in brief.full


def test_probe_requires_configured_agent(tmp_path):
    agent = _FakeQueryAgent(configured=False)
    assert asyncio.run(probe_business_sql(_pipeline(tmp_path, query_agent=agent, dsn=None))) == []


# ================================================================== Tier 3
def test_normalize_unicode_variants():
    assert normalize_text("O\u2019Leary") == "o'leary"
    assert normalize_text("O\uff07Leary") == "o'leary"
    assert normalize_text("non\u00a0breaking") == "non breaking"
    assert normalize_text("soft\u00adhyphen") == "softhyphen"


def _roster_seats() -> list[Seat]:
    return [
        Seat(id="patio11", name="Patrick McKenzie", seat_title="Pricing", domains=["pricing", "saas"], doctrine=[]),
        Seat(id="hormozi", name="Alex Hormozi", seat_title="Offers", domains=["offers", "marketing"], doctrine=[]),
        Seat(id="thompson", name="Ben Thompson", seat_title="Strategy", domains=["strategy", "platform"], doctrine=[]),
    ]


async def _triage(question: str, llm=None) -> dict:
    call = getattr(llm, "complete", None) if llm is not None else None
    router = BoardRouter(llm=call or llm)
    result = await router.triage(question, _roster_seats())
    return {
        "declined": result.declined,
        "reason": result.decline_reason,
        "seats": [s.id for s in result.seats],
        "matched": result.matched_by,
    }


def test_seat_selection_by_name_and_domain():
    r = asyncio.run(_triage("Ask the board about pricing our new product"))
    assert r["declined"] is False
    assert "patio11" in r["seats"]  # domain: pricing
    assert "hormozi" in r["seats"]  # domain: pricing (offers keyword)
    r2 = asyncio.run(_triage("What would Thompson say about our platform risk?"))
    assert "thompson" in r2["seats"] and r2["matched"]["thompson"] == "named"


def test_first_name_does_not_route():
    # "ben" alone must not route to Ben Thompson — surname required.
    r = asyncio.run(_triage("Should I hire Ben?"))
    assert "thompson" not in r["seats"]


def test_business_decision_never_declined_by_keywords():
    # The failure mode from the brief: "personal" must never decline this.
    r = asyncio.run(_triage("Should I cut this product loose? It feels personal — it's my first product."))
    assert r["declined"] is False


def test_fast_decline_medical_and_legal():
    r = asyncio.run(_triage("I have a persistent headache, should I see a doctor?"))
    assert r["declined"] is True
    r2 = asyncio.run(_triage("Am I at risk of a lawsuit over this contract?"))
    assert r2["declined"] is True


def test_llm_decline_gate_used():
    async def llm(messages, **kw):
        return '{"decline": true, "reason": "code review is not the board\'s job"}'
    r = asyncio.run(_triage("Review my python file for bugs", llm=llm))
    assert r["declined"] is True
    assert "code review" in r["reason"]


# ================================================================== Tier 4
class _FakeLLM:
    def __init__(self, responses=None, delay: float = 0.0):
        self.responses = responses or []
        self.calls = 0
        self.delay = delay

    async def complete(self, messages, temperature=0.3, max_tokens=1200, **kw):
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.responses:
            return self.responses.pop(0)
        return '{"position": "yes", "reasoning": "because", "citations": ["D1"], "confidence": 0.8, "would_change_mind": "if numbers changed", "abstain": false}'


class _FakeRepo:
    def usage_since(self, start, end):
        return {"cost_usd": 0.0, "calls": 0}


def _seat(seat_id="patio11", name="Patrick McKenzie", entries=None) -> Seat:
    entries = entries or [DoctrineEntry(id="D1", title="t", content="c", source="s")]
    return Seat(id=seat_id, name=name, seat_title="Pricing", domains=["pricing"], doctrine=entries)


def test_zero_ceiling_makes_zero_calls():
    llm = _FakeLLM()
    runner = MeetingRunner(llm=llm.complete, usage_repo=_FakeRepo(), cost_ceiling_usd=0.0)
    result = asyncio.run(runner.run("Q", [_seat()], "full", "short"))
    assert llm.calls == 0
    assert "ceiling is zero" in (result.error or "")


def test_seats_deduplicated():
    llm = _FakeLLM()
    runner = MeetingRunner(llm=llm.complete, usage_repo=_FakeRepo(), cost_ceiling_usd=1.0)
    result = asyncio.run(runner.run("Q", [_seat(), _seat()], "full", "short"))
    assert llm.calls == 1
    assert len(result.opinions) == 1


def test_timeout_does_not_kill_meeting(monkeypatch):
    import board.meeting as meeting_mod
    monkeypatch.setattr(meeting_mod, "SEAT_TIMEOUT_S", 0.05)
    llm = _FakeLLM(delay=0.5)
    runner = MeetingRunner(llm=llm.complete, usage_repo=_FakeRepo(), cost_ceiling_usd=1.0)
    result = asyncio.run(runner.run("Q", [_seat()], "full", "short"))
    assert result.opinions[0].error and "timeout" in result.opinions[0].error


def test_fabricated_citation_stripped_from_seat_output():
    llm = _FakeLLM(responses=['{"position": "yes", "citations": ["D9", "D1"], "abstain": false}'])
    runner = MeetingRunner(llm=llm.complete, usage_repo=_FakeRepo(), cost_ceiling_usd=1.0)
    result = asyncio.run(runner.run("Q", [_seat()], "full", "short"))
    op = result.opinions[0]
    assert op.citations == ["D1"]
    assert op.citations_rejected == ["D9"]
    assert op.unsourced is False


def test_unsourced_flag_from_seat_output():
    llm = _FakeLLM(responses=['{"position": "yes", "citations": [], "abstain": false}'])
    runner = MeetingRunner(llm=llm.complete, usage_repo=_FakeRepo(), cost_ceiling_usd=1.0)
    result = asyncio.run(runner.run("Q", [_seat()], "full", "short"))
    assert result.opinions[0].unsourced is True


def test_abstention_parsed_by_identity_not_truthiness():
    # The hostile-input rule: "false" as a string must be False, not truthy.
    op = _parse_seat_opinion(_seat(), '{"position": "no", "citations": [], "abstain": "false"}')
    assert op.abstain is False
    assert op.unsourced is True
    op2 = _parse_seat_opinion(_seat(), '{"position": "", "citations": [], "abstain": "true"}')
    assert op2.abstain is True
    assert op2.unsourced is False


# ================================================================== Tier 5
def test_one_voice_is_never_unanimous():
    opinions = [SeatOpinion(seat_id="a", seat_name="A", position="yes")]
    assert can_be_unanimous(opinions) is False
    opinions.append(SeatOpinion(seat_id="b", seat_name="B", position="yes"))
    assert can_be_unanimous(opinions) is True


def test_abstentions_never_count_as_voices():
    opinions = [
        SeatOpinion(seat_id="a", seat_name="A", position="yes"),
        SeatOpinion(seat_id="b", seat_name="B", abstain=True),
    ]
    assert can_be_unanimous(opinions) is False
    assert compute_verdict_counts(opinions) == (1, 2)


def test_spoken_summary_guard_fires():
    # The exact failure from the brief: verdict not unanimous, prose says unanimous.
    verdict = ChairVerdict(unanimous=False, split=["a: yes"], spoken_summary="The board is unanimous in cutting the product.")
    assert spoken_summary_matches(verdict) is False


def test_chair_guard_replaces_lying_prose():
    async def llm(messages, **kw):
        return '{"split": ["a: yes"], "synthesis": "x", "spoken_summary": "The board is unanimous."}'
    chair = ChairRunner(llm=llm)
    opinions = [SeatOpinion(seat_id="a", seat_name="A", position="yes")]
    verdict = asyncio.run(chair.run("Q", [_seat("a", "A")], opinions, "brief"))
    assert verdict.unanimous is False
    assert "unanim" not in verdict.spoken_summary.lower()
    assert any("unanim" in note for note in verdict.guard_notes)


# ================================================================== Tier 6
def test_retirement_blocks_id_reuse(tmp_path):
    store = BoardStore(tmp_path / "board.db")
    store.apply_edit("patio11", "D1", "retire")
    assert store.is_retired("patio11", "D1")
    with pytest.raises(ValueError):
        store.apply_edit("patio11", "D1", "add", content="new")


def test_substance_edit_drops_to_user(tmp_path):
    store = BoardStore(tmp_path / "board.db")
    store.apply_edit("patio11", "D1", "edit", content="changed doctrine", source="operator")
    seat = store.compose_seat(_seat())
    d1 = next(e for e in seat.doctrine if e.id == "D1")
    assert d1.status == "user"
    assert d1.content == "changed doctrine"
    # The verification state itself is never editable.
    with pytest.raises(ValueError):
        store.apply_edit("patio11", "D1", "edit", source="sourced")


def test_meeting_stored_with_citation_snapshots(tmp_path):
    from board.models import MeetingResult, ChairVerdict
    store = BoardStore(tmp_path / "board.db")
    meeting = MeetingResult(meeting_id="m1", ts="2026-01-01T00:00:00+00:00", question="Q")
    meeting.opinions = [SeatOpinion(seat_id="patio11", seat_name="Patrick McKenzie", position="yes", citations=["D1"])]
    meeting.verdict = ChairVerdict(unanimous=False, split=["patio11: yes"], synthesis="s", spoken_summary="split")
    snapshots = {"patio11": {"D1": {"title": "Charge more", "source": "Kalzumeus 2006"}}}
    store.save_meeting(meeting, snapshots)
    latest = store.latest_meeting()
    assert latest["id"] == "m1"
    op = latest["opinions"][0]
    assert json.loads(op["citation_sources"]) == {"D1": {"title": "Charge more", "source": "Kalzumeus 2006"}}


def _write(text: str, base: Path) -> Path:
    base.mkdir(parents=True, exist_ok=True)
    path = base / "tmp_test_dossier.md"
    path.write_text(text, encoding="utf-8")
    return path
