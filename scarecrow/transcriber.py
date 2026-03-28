"""Batch transcription runtime."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from scarecrow.runtime import ModelManager

log = logging.getLogger(__name__)

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
    ) -> None:
        self._bindings = bindings or TranscriberBindings()
        self._model_manager = model_manager or ModelManager()
        self._ready = False

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
        audio_s = len(audio) / 16000
        log.debug(
            "parakeet: %.1fs audio → %.0fms wall (RTF %.4f)",
            audio_s,
            wall * 1000,
            wall / audio_s if audio_s > 0 else 0,
        )
        return result.text.strip() if result.text else ""

    def transcribe_batch(
        self,
        audio: np.ndarray,
        batch_elapsed: int,
        *,
        emit_callback: bool = True,
    ) -> str | None:
        """Run parakeet on a drained recorder buffer.

        Returns the transcribed text, empty string if nothing was recognized,
        or None on error. The normal executor-driven path still emits the
        callback so the UI updates via call_from_thread.
        """
        try:
            text = self._transcribe_parakeet(audio)
        except Exception:
            log.exception("Batch transcription failed")
            self._emit_error(
                "batch",
                "Batch transcription failed. See debug log for the stack trace.",
            )
            return None

        if text and emit_callback and self._bindings.on_batch_result is not None:
            self._bindings.on_batch_result(text, batch_elapsed)

        return text if text else ""

    @property
    def is_ready(self) -> bool:
        return self._ready

    def _emit_error(self, source: str, message: str) -> None:
        log.error("%s: %s", source, message)
        if self._bindings.on_error is not None:
            self._bindings.on_error(source, message)
