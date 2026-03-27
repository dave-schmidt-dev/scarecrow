"""Batch transcription runtime built on faster-whisper."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from scarecrow import config
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
        self._batch_lock = threading.Lock()

    def bind(self, bindings: TranscriberBindings) -> None:
        """Attach UI/runtime callbacks."""
        self._bindings = bindings

    def prepare(self) -> None:
        """Load runtime prerequisites (env setup + tqdm lock)."""
        self._model_manager.prepare()
        self._ready = True

    def preload_batch_model(self) -> None:
        """Load the active backend's model eagerly."""
        if config.BACKEND == "parakeet":
            self._model_manager.get_parakeet_model()
        else:
            self._model_manager.get_batch_model()

    def shutdown(self, timeout: float | None = None) -> None:
        """Release runtime resources."""
        self._ready = False
        self._model_manager.release_models()

    def _transcribe_whisper(self, audio: np.ndarray, initial_prompt: str | None) -> str:
        """Run faster-whisper on audio. Returns joined segment text."""
        with self._batch_lock:
            model = self._model_manager.get_batch_model()
            extra_kwargs = {}
            if initial_prompt is not None:
                extra_kwargs["initial_prompt"] = initial_prompt
            segments, _ = model.transcribe(
                audio,
                language=config.LANGUAGE,
                beam_size=config.BEAM_SIZE,
                vad_filter=True,
                condition_on_previous_text=config.CONDITION_ON_PREVIOUS_TEXT,
                **extra_kwargs,
            )
        return " ".join(seg.text.strip() for seg in segments).strip()

    def _transcribe_parakeet(self, audio: np.ndarray) -> str:
        """Run Parakeet TDT model on audio. Returns text with punctuation."""
        import mlx.core as mx
        from parakeet_mlx.audio import get_logmel

        model = self._model_manager.get_parakeet_model()
        audio_mx = mx.array(audio)
        mel = get_logmel(audio_mx, model.preprocessor_config)
        result = model.generate(mel)[0]
        return result.text.strip() if result.text else ""

    def transcribe_batch(
        self,
        audio: np.ndarray,
        batch_elapsed: int,
        initial_prompt: str | None = None,
        *,
        emit_callback: bool = True,
    ) -> str | None:
        """Run the active backend model on a drained recorder buffer.

        Returns the transcribed text, empty string if nothing was recognized,
        or None on error. The normal executor-driven path still emits the
        callback so the UI updates via call_from_thread.
        """
        try:
            if config.BACKEND == "parakeet":
                text = self._transcribe_parakeet(audio)
            else:
                text = self._transcribe_whisper(audio, initial_prompt)
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

    @property
    def has_active_worker(self) -> bool:
        """Always False — no worker thread in batch-only mode."""
        return False

    def _emit_error(self, source: str, message: str) -> None:
        log.error("%s: %s", source, message)
        if self._bindings.on_error is not None:
            self._bindings.on_error(source, message)
