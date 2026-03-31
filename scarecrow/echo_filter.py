"""Bidirectional echo suppression for dual-channel audio.

When not using headphones, the mic picks up speaker output, causing both
channels to transcribe the same remote speech. Since mic and sys drain
at different times through a shared single-threaded executor, either
source may transcribe the overlapping content first. This filter works
bidirectionally — whichever source transcribes first wins, the duplicate
from the other source is suppressed.

Detection strategies:
1. Consecutive-run matching — catches partial echoes where mic and sys
   drain at different times with offset boundaries.
2. Jaccard word-set similarity — catches near-identical full segments.
"""

from __future__ import annotations

import logging
import threading
import time

log = logging.getLogger(__name__)

# Minimum consecutive words found in the other source to flag as echo.
_MIN_CONSECUTIVE_RUN = 8


class EchoFilter:
    """Bidirectional echo suppression between mic and sys transcripts."""

    def __init__(
        self,
        similarity_threshold: float = 0.6,
        window_seconds: float = 35.0,
        min_consecutive_run: int = _MIN_CONSECUTIVE_RUN,
    ) -> None:
        self._threshold = similarity_threshold
        self._window = window_seconds
        self._min_run = min_consecutive_run
        # Each entry: (timestamp, word_set, word_list)
        self._recent_sys: list[tuple[float, set[str], list[str]]] = []
        self._recent_mic: list[tuple[float, set[str], list[str]]] = []
        self._lock = threading.Lock()

    def _prune(
        self, entries: list[tuple[float, set[str], list[str]]], cutoff: float
    ) -> list[tuple[float, set[str], list[str]]]:
        return [(t, ws, wl) for t, ws, wl in entries if t >= cutoff]

    def _record(
        self,
        entries: list[tuple[float, set[str], list[str]]],
        text: str,
    ) -> None:
        words = text.lower().split()
        if not words:
            return
        now = time.monotonic()
        with self._lock:
            entries.append((now, set(words), words))
            cutoff = now - self._window
            entries[:] = self._prune(entries, cutoff)

    def record_sys(self, text: str) -> None:
        """Register a sys transcript for future echo detection."""
        self._record(self._recent_sys, text)

    def record_mic(self, text: str) -> None:
        """Register a mic transcript for future echo detection."""
        self._record(self._recent_mic, text)

    def _is_duplicate(
        self,
        text_words: list[str],
        text_word_set: set[str],
        entries: list[tuple[float, set[str], list[str]]],
        label: str,
        text_preview: str,
    ) -> bool:
        """Check if text duplicates any entry in the given list."""
        now = time.monotonic()
        cutoff = now - self._window
        for ts, other_word_set, _other_word_list in entries:
            if ts < cutoff:
                continue
            if not other_word_set:
                continue

            # Strategy 1: consecutive-run matching.
            if len(text_words) >= self._min_run:
                run = 0
                max_run = 0
                for w in text_words:
                    if w in other_word_set:
                        run += 1
                        if run > max_run:
                            max_run = run
                    else:
                        run = 0
                if max_run >= self._min_run:
                    log.debug(
                        "Echo suppressed %s (run=%d): %s",
                        label,
                        max_run,
                        text_preview,
                    )
                    return True

            # Strategy 2: Jaccard similarity.
            intersection = text_word_set & other_word_set
            union = text_word_set | other_word_set
            similarity = len(intersection) / len(union) if union else 0.0
            if similarity >= self._threshold:
                log.debug(
                    "Echo suppressed %s (%.0f%% Jaccard): %s",
                    label,
                    similarity * 100,
                    text_preview,
                )
                return True
        return False

    def is_echo(self, mic_text: str) -> bool:
        """Return True if mic_text duplicates recent sys audio."""
        words = mic_text.lower().split()
        if len(words) < 3:
            return False
        with self._lock:
            return self._is_duplicate(
                words, set(words), self._recent_sys, "mic→sys", mic_text[:80]
            )

    def is_sys_echo(self, sys_text: str) -> bool:
        """Return True if sys_text duplicates recent mic audio."""
        words = sys_text.lower().split()
        if len(words) < 3:
            return False
        with self._lock:
            return self._is_duplicate(
                words, set(words), self._recent_mic, "sys→mic", sys_text[:80]
            )
