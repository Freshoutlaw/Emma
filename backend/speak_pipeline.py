"""Hold-one-ahead speak-segment pipeline.

Turns an LLM token stream into ``speak_segment`` events for the pipelined TTS
path.  Sentences are detected with a :class:`StreamingSentenceSplitter`, but
emitted with a one-sentence lag ("hold-one-ahead"): a completed sentence is
only emitted once the *next* sentence arrives (proving it wasn't the last),
and the final sentence is emitted by :meth:`SpeakPipeline.finish` with
``is_final=True``.  The client can therefore start fetching/synthesizing
segment N while the LLM is still generating sentence N+1.

Segment ids come from the TTS store: short canned replies ("Yes.", "No.",
"Okay.") resolve to their stable pre-cached common id; everything else gets a
per-turn unique id and is recorded for on-demand synthesis.
"""

from __future__ import annotations

import uuid
from typing import Optional

from backend.sentence_splitter import StreamingSentenceSplitter
from backend.tts_store import TTSStore, tts_store


class SpeakPipeline:
    """Feed streamed tokens; collect ``speak_segment`` event dicts.

    Usage::

        speak = SpeakPipeline()
        async for token in llm_stream:
            for event in speak.feed(token):
                yield event
        for event in speak.finish():
            yield event
    """

    def __init__(self, store: Optional[TTSStore] = None) -> None:
        self._splitter = StreamingSentenceSplitter()
        self._store = store or tts_store
        # Stable per-turn id shared by every segment of this narration.
        self.base_turn_id: str = uuid.uuid4().hex
        self._seq = 0
        self._held_text: Optional[str] = None
        self._held_seq: Optional[int] = None

    def feed(self, token: str) -> list[dict]:
        """Feed one streamed token.

        Returns the ``speak_segment`` events for sentences that completed.
        With hold-one-ahead that is at most the *previously* held sentence
        (``is_final=False``); the newly completed sentence is held instead.
        """
        events: list[dict] = []
        for sentence in self._splitter.feed(token):
            if self._held_text is not None:
                events.append(self._emit(self._held_text, self._held_seq, is_final=False))
            self._held_text = sentence
            self._held_seq = self._seq
            self._seq += 1
        return events

    def finish(self) -> list[dict]:
        """Flush the splitter buffer and emit the final segment.

        Returns the held sentence (or the flushed partial, if nothing was
        held) as the last segment with ``is_final=True``.
        """
        events: list[dict] = []
        remaining = self._splitter.flush()
        if remaining:
            # A partial sentence appeared after the held one — the held
            # sentence is therefore not final.
            if self._held_text is not None:
                events.append(self._emit(self._held_text, self._held_seq, is_final=False))
            self._held_text = remaining
            self._held_seq = self._seq
            self._seq += 1
        if self._held_text is not None:
            events.append(self._emit(self._held_text, self._held_seq, is_final=True))
        return events

    def _emit(self, text: str, seq: Optional[int], is_final: bool) -> dict:
        sid = self._store.resolve_segment_id(self.base_turn_id, seq if seq is not None else 0, text)
        return {
            "type": "speak_segment",
            "turn_id": sid,
            "base_turn_id": self.base_turn_id,
            "seq": seq,
            "is_final": is_final,
        }
