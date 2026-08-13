"""Wake-word detection — improved energy VAD + keyword matching.

Uses improved energy-based VAD with sustained silence detection, speech state
tracking, and max-duration safety cap to prevent premature cutoffs during speech.
"""

from __future__ import annotations

import math
import struct
import time
from typing import Optional


class WakeWordEngine:
    def __init__(
        self,
        keyword: str = "emma",
        threshold: float = 0.02,
        sensitivity: float = 0.5,
        silence_duration_ms: int = 1500,
        max_duration_ms: int = 60000,
    ) -> None:
        self.keyword = keyword.lower()
        self.threshold = threshold
        self.sensitivity = max(0.0, min(1.0, sensitivity))
        self.silence_duration_ms = silence_duration_ms
        self.max_duration_ms = max_duration_ms
        
        # Speech state tracking
        self.speech_started = False
        self.silent_frames = 0
        self.utterance_start_time: Optional[float] = None
        
        # Audio processing config
        self.frame_duration_ms = 30  # 30ms frames
        self.silence_frames_threshold = int(silence_duration_ms / self.frame_duration_ms)

    def contains_wake(self, transcript: str) -> bool:
        """True if the transcript contains the wake word (forgiving match)."""
        text = (transcript or "").lower()
        return self.keyword in text or f"hey {self.keyword}" in text

    def detect(self, pcm_frame: bytes, sample_width: int = 2) -> bool:
        """Improved energy-based voice activity detection with sustained silence tracking.

        Returns True when speech energy is present, but only ends utterance after
        sustained silence rather than first quiet frame.
        """
        if not pcm_frame or sample_width not in (1, 2):
            return False
        
        # Check max duration safety cap
        if self.utterance_start_time is not None:
            elapsed_ms = (time.time() - self.utterance_start_time) * 1000
            if elapsed_ms >= self.max_duration_ms:
                # Max duration exceeded, end utterance
                self._reset_state()
                return False
        
        count = len(pcm_frame) // sample_width
        if count == 0:
            return False
        if sample_width == 2:
            values = struct.unpack(f"<{count}h", pcm_frame[: count * 2])
        else:
            values = struct.unpack(f"<{count}b", pcm_frame[:count])
        rms = math.sqrt(sum((v / 32768.0) ** 2 for v in values) / count)
        
        has_energy = rms >= self.threshold * (1.0 + self.sensitivity)
        
        if has_energy:
            if not self.speech_started:
                self.speech_started = True
                self.utterance_start_time = time.time()
            self.silent_frames = 0
            return True
        else:
            if self.speech_started:
                self.silent_frames += 1
                if self.silent_frames >= self.silence_frames_threshold:
                    # Sustained silence detected, end utterance
                    self._reset_state()
                    return False
            return False

    def _reset_state(self) -> None:
        """Reset speech tracking state after utterance ends."""
        self.speech_started = False
        self.silent_frames = 0
        self.utterance_start_time = None

    def is_utterance_complete(self) -> bool:
        """Check if the current utterance should be considered complete."""
        return (not self.speech_started and 
                self.silent_frames >= self.silence_frames_threshold)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<WakeWordEngine keyword={self.keyword!r} silence={self.silence_duration_ms}ms max={self.max_duration_ms}ms>"
