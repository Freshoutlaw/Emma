"""Streaming sentence splitter — buffers LLM token deltas and yields
complete sentences for per-segment TTS.

The LLM stream yields individual tokens ("Hello", " ", "world", ".", " ").
This splitter buffers them and yields when it detects a sentence boundary
(.!? followed by whitespace or end-of-string).  Not perfect (will split
"Mr. Smith" mid-name) but good enough for TTS — the engine handles
mid-sentence breaks gracefully.
"""

from __future__ import annotations

import re
from typing import Iterator

# Sentence boundary: .!? followed by whitespace or end-of-string.
_BOUNDARY_RE = re.compile(r"[.!?](?:\s|$)")


class StreamingSentenceSplitter:
    """Buffer tokens, yield complete sentences.

    Usage::

        splitter = StreamingSentenceSplitter()
        for token in token_stream:
            for sentence in splitter.feed(token):
                yield sentence          # complete sentence
        remaining = splitter.flush()    # partial at stream end
        if remaining:
            yield remaining
    """

    def __init__(self) -> None:
        self._buf = ""

    def feed(self, token: str) -> list[str]:
        """Feed one token.  Returns zero or more complete sentences."""
        self._buf += token
        sentences: list[str] = []
        while True:
            m = _BOUNDARY_RE.search(self._buf)
            if not m:
                break
            end = m.end()
            sentence = self._buf[:end].strip()
            if sentence:
                sentences.append(sentence)
            self._buf = self._buf[end:]
        return sentences

    def flush(self) -> str:
        """Return any remaining text (the final partial sentence)."""
        remaining = self._buf.strip()
        self._buf = ""
        return remaining
