"""TTL'd in-memory store for per-sentence TTS segment text.

Each speak_segment event records its text here so the /api/tts/<id>
endpoint can synthesize on demand.  Segments expire after TTL_S seconds
(3600 by default - extended from 120 for better caching) — long enough for
the client to fetch, short enough to avoid leaking conversation content
into memory.

OPTIMIZATIONS:
- Extended TTL from 120s to 3600s for better caching
- Pre-generated common responses cache
"""

from __future__ import annotations

import time
import threading
from typing import Optional

TTL_S = 3600.0  # Extended from 120s to 1 hour for better caching

# Common entries live 2x as long as regular segments.
COMMON_TTL_MULT = 2.0

# Common responses to pre-generate and cache
COMMON_RESPONSES = {
    "yes": "Yes.",
    "no": "No.",
    "i don't know": "I don't know.",
    "okay": "Okay.",
    "understood": "Understood.",
    "got it": "Got it.",
    "sure": "Sure.",
    "done": "Done.",
    "complete": "Complete.",
    "ready": "Ready.",
}


class TTSStore:
    """Thread-safe, TTL'd dict keyed by segment_id → (text, expiry_ts)."""

    def __init__(self, ttl: float = TTL_S) -> None:
        self._ttl = ttl
        self._store: dict[str, tuple[str, float]] = {}
        self._lock = threading.Lock()
        # Cache for common responses — keyed by both the trigger word and the
        # response sentence, both normalized (lowercased, stripped).
        self._common_cache: dict[str, str] = {}
        # Segment text per common id, for re-priming expired entries.
        self._common_texts: dict[str, str] = {}
        self._initialize_common_cache()

    def _initialize_common_cache(self) -> None:
        """Pre-cache common responses with 2x TTL."""
        now = time.monotonic()
        for trigger, response in COMMON_RESPONSES.items():
            # Use hash of the trigger word as segment_id for common responses
            segment_id = f"common:{hash(trigger)}"
            self._store[segment_id] = (response, now + self._ttl * COMMON_TTL_MULT)
            self._common_texts[segment_id] = response
            for key in {trigger, response.strip().lower()}:
                self._common_cache[key] = segment_id

    def get_common_id(self, text: str) -> Optional[str]:
        """Segment id for a common response sentence (or its trigger word),
        or None when the text isn't a canned reply.

        Re-primes the entry if it has expired so a stable id never 404s.
        """
        sid = self._common_cache.get(text.strip().lower())
        if sid is None:
            return None
        now = time.monotonic()
        with self._lock:
            entry = self._store.get(sid)
            if entry is None or entry[1] < now:
                self._store[sid] = (self._common_texts[sid], now + self._ttl * COMMON_TTL_MULT)
        return sid

    def resolve_segment_id(self, base_turn_id: str, seq: int, text: str) -> str:
        """Segment id for a completed sentence in the pipelined speak path.

        Short canned replies ("Yes.", "No.", "Okay.") map to their pre-cached
        common id — stable across turns, so the client's second fetch is served
        from the browser HTTP cache without another Deepgram call.  Everything
        else gets a per-turn unique id and is recorded here so the /api/tts/<id>
        endpoint can synthesize it on demand.
        """
        common_id = self.get_common_id(text)
        if common_id is not None:
            return common_id
        sid = f"{base_turn_id}::{seq}"
        self.record(sid, text)
        return sid

    def record(self, segment_id: str, text: str) -> None:
        now = time.monotonic()
        with self._lock:
            self._store[segment_id] = (text, now + self._ttl)
            self._prune(now)

    def get(self, segment_id: str) -> Optional[str]:
        now = time.monotonic()
        with self._lock:
            self._prune(now)
            entry = self._store.get(segment_id)
            return entry[0] if entry else None

    def _prune(self, now: float) -> None:
        expired = [k for k, (_, exp) in self._store.items() if exp < now]
        for k in expired:
            self._store.pop(k, None)


# Module-level singleton — shared across the router (writer) and the TTS
# endpoint (reader).  Created at import time; no setup needed.
tts_store = TTSStore()
