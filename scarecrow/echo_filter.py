"""Transcript-level echo suppression for dual-channel audio.

When not using headphones, the mic picks up speaker output, causing both
channels to transcribe the same remote speech. This filter detects and
suppresses mic transcripts that duplicate recent sys transcripts using
Jaccard word-set similarity.
"""

from __future__ import annotations

import logging
import threading
import time

log = logging.getLogger(__name__)


class EchoFilter:
    """Suppresses mic transcripts that duplicate recent sys transcripts."""

    def __init__(
        self, similarity_threshold: float = 0.6, window_seconds: float = 15.0
    ) -> None:
        self._threshold = similarity_threshold
        self._window = window_seconds
        self._recent_sys: list[tuple[float, set[str]]] = []
        self._lock = threading.Lock()

    def record_sys(self, text: str) -> None:
        """Register a sys transcript for future echo detection."""
        words = set(text.lower().split())
        if not words:
            return
        now = time.monotonic()
        with self._lock:
            self._recent_sys.append((now, words))
            # Prune old entries
            cutoff = now - self._window
            self._recent_sys = [(t, w) for t, w in self._recent_sys if t >= cutoff]

    def is_echo(self, mic_text: str) -> bool:
        """Return True if mic_text is likely an echo of recent sys audio."""
        mic_words = set(mic_text.lower().split())
        if len(mic_words) < 3:
            return False  # Too short to judge

        now = time.monotonic()
        with self._lock:
            cutoff = now - self._window
            for ts, sys_words in self._recent_sys:
                if ts < cutoff:
                    continue
                if not sys_words:
                    continue
                intersection = mic_words & sys_words
                union = mic_words | sys_words
                similarity = len(intersection) / len(union) if union else 0.0
                if similarity >= self._threshold:
                    log.debug(
                        "Echo suppressed (%.0f%% overlap): %s",
                        similarity * 100,
                        mic_text[:80],
                    )
                    return True
        return False
