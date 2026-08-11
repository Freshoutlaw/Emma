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
        # Cache for common responses
        self._common_cache: dict[str, str] = {}
        self._initialize_common_cache()

    def _initialize_common_cache(self) -> None:
        """Pre-cache common responses with long TTL."""
        now = time.monotonic()
        for text, response in COMMON_RESPONSES.items():
            # Use hash of text as segment_id for common responses
            segment_id = f"common:{hash(text)}"
            self._store[segment_id] = (response, now + self._ttl * 2)  # 2x TTL for common
            self._common_cache[text] = segment_id

    def get_common_id(self, text: str) -> Optional[str]:
        """Get segment_id for a common response if cached."""
        return self._common_cache.get(text.lower())

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
