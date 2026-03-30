"""Batch transcription runtime."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from scarecrow import config as _config_module
from scarecrow.config import Config
from scarecrow.runtime import ModelManager

log = logging.getLogger(__name__)

_MAX_RETRIES = 2  # total attempts = 3 (1 initial + 2 retries)
_RETRY_DELAY = 0.5  # seconds between retries

BatchCallback = Callable[[str, int], None]
ErrorCallback = Callable[[str, str], None]


@dataclass(slots=True)
class TranscriberBindings:
    """Callbacks for UI/runtime integration."""

    on_batch_result: BatchCallback | None = None
    on_error: ErrorCallback | None = None


class Transcriber:
    """Owns batch transcription for a recording session."""

    def __init__(
        self,
        bindings: TranscriberBindings | None = None,
        *,
        model_manager: ModelManager | None = None,
        cfg: Config | None = None,
    ) -> None:
        self._bindings = bindings or TranscriberBindings()
        self._model_manager = model_manager or ModelManager(cfg=cfg)
        self._cfg = cfg or _config_module.config
        self._ready = False
        self._consecutive_failures = 0

    def bind(self, bindings: TranscriberBindings) -> None:
        """Attach UI/runtime callbacks."""
        self._bindings = bindings

    def prepare(self) -> None:
        """Load runtime prerequisites."""
        self._model_manager.prepare()
        self._ready = True

    def preload_batch_model(self) -> None:
        """Load the active backend's model eagerly."""
        self._model_manager.get_parakeet_model()

    def shutdown(self, timeout: float | None = None) -> None:
        """Release runtime resources."""
        self._ready = False
        self._model_manager.release_models()

    def _transcribe_parakeet(self, audio: np.ndarray) -> str:
        """Run Parakeet TDT model on audio. Returns text with punctuation."""
        import time

        import mlx.core as mx
        from parakeet_mlx.audio import get_logmel

        model = self._model_manager.get_parakeet_model()
        t0 = time.perf_counter()
        audio_mx = mx.array(audio)
        mel = get_logmel(audio_mx, model.preprocessor_config)
        result = model.generate(mel)[0]
        wall = time.perf_counter() - t0
        audio_s = len(audio) / self._cfg.SAMPLE_RATE
        log.debug(
            "parakeet: %.1fs audio → %.0fms wall (RTF %.4f)",
            audio_s,
            wall * 1000,
            wall / audio_s if audio_s > 0 else 0,
        )
        return result.text.strip() if result.text else ""

    @staticmethod
    def _is_hallucination(text: str) -> bool:
        """Detect repeated-token hallucination (e.g., 'the the the the')."""
        words = text.split()
        return len(words) >= 3 and len(set(w.lower() for w in words)) == 1

    def transcribe_batch(
        self,
        audio: np.ndarray,
        batch_elapsed: int,
        *,
        emit_callback: bool = True,
        max_retries: int | None = None,
    ) -> str | None:
        """Run parakeet on a drained recorder buffer.

        Returns the transcribed text, empty string if nothing was recognized,
        or None on error. Retries up to max_retries times before giving up
        (defaults to _MAX_RETRIES). Pass max_retries=0 to skip retries, e.g.
        during shutdown. The normal executor-driven path still emits the
        callback so the UI updates via call_from_thread.
        """
        retries = max_retries if max_retries is not None else _MAX_RETRIES
        for attempt in range(retries + 1):
            try:
                text = self._transcribe_parakeet(audio)
                # Success — reset failure tracking
                self._consecutive_failures = 0
                if text and self._is_hallucination(text):
                    log.debug("Filtered hallucination: %r", text)
                    return ""
                if (
                    text
                    and emit_callback
                    and self._bindings.on_batch_result is not None
                ):
                    self._bindings.on_batch_result(text, batch_elapsed)
                return text if text else ""
            except Exception:
                log.exception(
                    "Batch transcription failed (attempt %d/%d)",
                    attempt + 1,
                    retries + 1,
                )
                if attempt < retries:
                    time.sleep(_RETRY_DELAY)

        # All retries exhausted
        self._consecutive_failures += 1
        self._emit_error(
            "batch",
            "Batch transcription failed after retries. Audio is still recording.",
        )
        return None

    @property
    def is_ready(self) -> bool:
        return self._ready

    @property
    def consecutive_failures(self) -> int:
        """Number of consecutive batch transcription failures."""
        return self._consecutive_failures

    def _emit_error(self, source: str, message: str) -> None:
        log.error("%s: %s", source, message)
        if self._bindings.on_error is not None:
            self._bindings.on_error(source, message)
