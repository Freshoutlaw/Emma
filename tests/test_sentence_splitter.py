"""Boundary tests for the streaming sentence splitter."""

from backend.sentence_splitter import StreamingSentenceSplitter


def test_single_complete_sentence():
    s = StreamingSentenceSplitter()
    assert s.feed("Hello world.") == ["Hello world."]
    assert s.flush() == ""


def test_multiple_sentences_in_one_token():
    s = StreamingSentenceSplitter()
    assert s.feed("One. Two! Three?") == ["One.", "Two!", "Three?"]
    assert s.flush() == ""


def test_sentence_built_from_many_tokens():
    s = StreamingSentenceSplitter()
    assert s.feed("Hel") == []
    assert s.feed("lo ") == []
    # '.' at end-of-buffer is a boundary — the sentence completes here.
    assert s.feed("world.") == ["Hello world."]
    assert s.flush() == ""


def test_boundary_requires_whitespace_or_end():
    s = StreamingSentenceSplitter()
    # "Mr." is not a boundary (period followed by letter) — documented imperfection.
    assert s.feed("Mr. Smith is here.") == ["Mr.", "Smith is here."]


def test_sentence_completes_exactly_at_token_end():
    s = StreamingSentenceSplitter()
    assert s.feed("Yes.") == ["Yes."]
    assert s.feed(" No.") == ["No."]
    assert s.flush() == ""


def test_flush_returns_trailing_partial():
    s = StreamingSentenceSplitter()
    assert s.feed("Hello wor") == []
    assert s.flush() == "Hello wor"
    assert s.flush() == ""  # idempotent


def test_whitespace_only_produces_no_sentences():
    s = StreamingSentenceSplitter()
    assert s.feed("   ") == []
    assert s.flush() == ""


def test_punctuation_boundaries_variants():
    s = StreamingSentenceSplitter()
    assert s.feed("What? ") == ["What?"]
    assert s.feed("Wow!") == ["Wow!"]
    assert s.feed(" Done.") == ["Done."]
    assert s.flush() == ""
