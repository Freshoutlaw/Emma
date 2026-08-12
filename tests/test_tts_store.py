"""Tests for the TTS segment store: common-response routing and TTL behavior."""

import time

from backend.tts_store import TTSStore, COMMON_RESPONSES


def test_common_id_resolves_by_trigger_and_sentence():
    store = TTSStore()
    for trigger, response in COMMON_RESPONSES.items():
        by_trigger = store.get_common_id(trigger)
        by_sentence = store.get_common_id(response)
        assert by_trigger is not None, f"trigger {trigger!r} should map to a cached id"
        assert by_trigger == by_sentence, "trigger and its sentence must share one id"
        assert by_trigger.startswith("common:")


def test_common_id_is_case_and_space_insensitive():
    store = TTSStore()
    assert store.get_common_id("Yes.") == store.get_common_id("  yes. ")
    assert store.get_common_id("YES") == store.get_common_id("yes")
    assert store.get_common_id("Not a canned reply") is None
    assert store.get_common_id("") is None


def test_resolve_segment_id_uses_common_id_without_recording():
    store = TTSStore()
    common_id = store.get_common_id("Yes.")
    sid = store.resolve_segment_id("turnabc", 3, "Yes.")
    assert sid == common_id
    # Common ids are pre-cached; resolving them must not churn the store entry.
    assert store.get(sid) == "Yes."


def test_resolve_segment_id_records_unique_ids_for_normal_text():
    store = TTSStore()
    sid1 = store.resolve_segment_id("turnabc", 0, "Hello there.")
    sid2 = store.resolve_segment_id("turnabc", 1, "How are you?")
    assert sid1 == "turnabc::0"
    assert sid2 == "turnabc::1"
    assert store.get(sid1) == "Hello there."
    assert store.get(sid2) == "How are you?"


def test_common_id_repairs_itself_after_expiry():
    store = TTSStore(ttl=0.05)
    sid = store.get_common_id("No.")
    time.sleep(0.08)
    # Expired — a fresh lookup re-primes so the id never 404s.
    assert store.get_common_id("No.") == sid
    assert store.get(sid) == "No."


def test_regular_segments_expire_and_are_pruned():
    store = TTSStore(ttl=0.05)
    store.record("seg1", "text one")
    store.record("seg2", "text two")
    time.sleep(0.08)
    assert store.get("seg1") is None
    assert store.get("seg2") is None
    assert store.get_common_id("yes") is not None, "common entries must outlive regular TTL"
