"""Wake-word detection — lightweight energy VAD + keyword matching.

No heavy dependencies: `detect()` scores PCM frames by RMS energy, and
`contains_wake()` does forgiving substring matching on transcripts. A vosk
keyword model can be dropped in later without changing the interface.
"""

from __future__ import annotations

import math
import struct
from typing import Optional


class WakeWordEngine:
    def __init__(self, keyword: str = "emma", threshold: float = 0.02, sensitivity: float = 0.5) -> None:
        self.keyword = keyword.lower()
        self.threshold = threshold
        self.sensitivity = max(0.0, min(1.0, sensitivity))

    def contains_wake(self, transcript: str) -> bool:
        """True if the transcript contains the wake word (forgiving match)."""
        text = (transcript or "").lower()
        return self.keyword in text or f"hey {self.keyword}" in text

    def detect(self, pcm_frame: bytes, sample_width: int = 2) -> bool:
        """Energy-based voice activity detection on one raw PCM frame.

        Returns True when speech energy is present (a candidate wake window).
        """
        if not pcm_frame or sample_width not in (1, 2):
            return False
        count = len(pcm_frame) // sample_width
        if count == 0:
            return False
        if sample_width == 2:
            values = struct.unpack(f"<{count}h", pcm_frame[: count * 2])
        else:
            values = struct.unpack(f"<{count}b", pcm_frame[:count])
        rms = math.sqrt(sum((v / 32768.0) ** 2 for v in values) / count)
        return rms >= self.threshold * (1.0 + self.sensitivity)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<WakeWordEngine keyword={self.keyword!r}>"
