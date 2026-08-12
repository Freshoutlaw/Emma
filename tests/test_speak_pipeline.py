"""Boundary tests for the hold-one-ahead speak pipeline (segment emission)."""

from backend.speak_pipeline import SpeakPipeline
from backend.tts_store import TTSStore


def _pipeline():
    return SpeakPipeline(store=TTSStore())


def test_empty_stream_emits_nothing():
    p = _pipeline()
    assert p.feed("") == []
    assert p.finish() == []


def test_single_sentence_emitted_final_on_finish():
    p = _pipeline()
    assert p.feed("Hello world.") == []  # held, not emitted yet
    events = p.finish()
    assert len(events) == 1
    ev = events[0]
    assert ev["type"] == "speak_segment"
    assert ev["base_turn_id"] == p.base_turn_id
    assert ev["seq"] == 0
    assert ev["is_final"] is True
    assert ev["turn_id"] == f"{p.base_turn_id}::0"
    assert p._store.get(ev["turn_id"]) == "Hello world."


def test_two_sentences_hold_one_ahead():
    p = _pipeline()
    events = p.feed("One. Two.")
    assert len(events) == 1  # "One." emitted only once "Two." completes
    assert events[0]["seq"] == 0
    assert events[0]["is_final"] is False
    final = p.finish()
    assert len(final) == 1
    assert final[0]["seq"] == 1
    assert final[0]["is_final"] is True


def test_three_sentences_one_token():
    p = _pipeline()
    events = p.feed("A. B. C.")
    assert [e["seq"] for e in events] == [0, 1]
    assert all(not e["is_final"] for e in events)
    final = p.finish()
    assert [e["seq"] for e in final] == [2]
    assert final[0]["is_final"] is True


def test_sentence_across_tokens_emitted_when_next_completes():
    p = _pipeline()
    assert p.feed("Hel") == []
    assert p.feed("lo ") == []
    assert p.feed("world.") == []  # completes, but held
    # Next sentence completes -> held "Hello world." is emitted as non-final.
    events = p.feed(" Next!")
    assert len(events) == 1
    assert events[0]["seq"] == 0
    assert events[0]["is_final"] is False
    final = p.finish()
    assert len(final) == 1
    assert final[0]["seq"] == 1
    assert final[0]["is_final"] is True


def test_trailing_partial_flushed_as_final():
    p = _pipeline()
    assert p.feed("Hello world") == []
    events = p.finish()
    assert len(events) == 1
    assert events[0]["is_final"] is True
    assert events[0]["seq"] == 0
    assert p._store.get(events[0]["turn_id"]) == "Hello world"


def test_held_sentence_plus_partial_flush():
    p = _pipeline()
    assert p.feed("One. Two") == []  # "One." completes and is held
    events = p.finish()  # flush "Two" — "One." is not final
    assert len(events) == 2
    assert (events[0]["seq"], events[0]["is_final"]) == (0, False)
    assert (events[1]["seq"], events[1]["is_final"]) == (1, True)


def test_common_response_routes_to_cached_id():
    p = _pipeline()
    assert p.feed("Yes.") == []
    events = p.finish()
    assert len(events) == 1
    turn_id = events[0]["turn_id"]
    assert turn_id == p._store.get_common_id("Yes.")
    assert p._store.get(turn_id) == "Yes."


def test_common_and_normal_segments_mixed():
    p = _pipeline()
    events = p.feed("Yes. Let me check.")
    assert len(events) == 1
    assert events[0]["turn_id"] == p._store.get_common_id("Yes.")
    final = p.finish()
    assert len(final) == 1
    assert final[0]["turn_id"] == f"{p.base_turn_id}::1"
    assert p._store.get(final[0]["turn_id"]) == "Let me check."


def test_seqs_monotonic_and_turn_stable():
    p = _pipeline()
    feed_events = p.feed("One. Two. Three. Four. Five.")
    final = p.finish()
    all_events = feed_events + final
    assert [e["seq"] for e in all_events] == [0, 1, 2, 3, 4]
    assert all(e["base_turn_id"] == p.base_turn_id for e in all_events)
    assert all(e["type"] == "speak_segment" for e in all_events)
    assert sum(1 for e in all_events if e["is_final"]) == 1
