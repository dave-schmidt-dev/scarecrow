"""Live transcription runtime built on Silero VAD and faster-whisper."""

from __future__ import annotations

import logging
import queue
import threading
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path

import numpy as np
import onnxruntime

from scarecrow import config
from scarecrow.runtime import ModelManager

log = logging.getLogger(__name__)

RealtimeCallback = Callable[[str], None]
BatchCallback = Callable[[str, int], None]
ErrorCallback = Callable[[str, str], None]


@dataclass(slots=True)
class TranscriberBindings:
    """Callbacks for UI/runtime integration."""

    on_realtime_update: RealtimeCallback | None = None
    on_realtime_stabilized: RealtimeCallback | None = None
    on_batch_result: BatchCallback | None = None
    on_error: ErrorCallback | None = None


class _SileroVAD:
    """Silero VAD wrapper using ONNX runtime. No torch dependency."""

    def __init__(self) -> None:
        onnx_path = str(Path(__file__).parent / "models" / "silero_vad.onnx")
        opts = onnxruntime.SessionOptions()
        opts.inter_op_num_threads = 1
        opts.intra_op_num_threads = 1
        opts.log_severity_level = 3
        self._session = onnxruntime.InferenceSession(onnx_path, sess_options=opts)
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros((1, 64), dtype=np.float32)
        self._sr = np.array(16000, dtype=np.int64)

    def __call__(self, chunk: np.ndarray) -> float:
        """Run VAD on exactly 512 float32 samples and return a probability."""
        if chunk.shape[-1] != config.VAD_CHUNK_SAMPLES:
            msg = f"Expected {config.VAD_CHUNK_SAMPLES} samples, got {chunk.shape[-1]}"
            raise ValueError(msg)

        x = np.concatenate([self._context, chunk.reshape(1, -1)], axis=1)
        out, new_state = self._session.run(
            None,
            {"input": x, "state": self._state, "sr": self._sr},
        )
        self._state = new_state
        self._context = x[:, -64:]
        return float(out[0, 0])

    def reset_states(self) -> None:
        """Reset hidden state between utterances."""
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros((1, 64), dtype=np.float32)

    def close(self) -> None:
        """Release the ONNX session explicitly for deterministic teardown."""
        self._session = None

    def __del__(self) -> None:
        self.close()


class _VadState(Enum):
    SILENCE = auto()
    SPEECH = auto()


class Transcriber:
    """Owns realtime and batch transcription for a recording session."""

    def __init__(
        self,
        bindings: TranscriberBindings | None = None,
        *,
        model_manager: ModelManager | None = None,
    ) -> None:
        self._bindings = bindings or TranscriberBindings()
        self._model_manager = model_manager or ModelManager()
        self._vad: _SileroVAD | None = None
        self._model = None
        self._realtime_model = None
        self._queue: queue.Queue[np.ndarray | None] = queue.Queue(maxsize=200)
        self._worker: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._ready = False
        self._audio_drop_reported = False  # bool; GIL-atomic in CPython
        self._batch_lock = threading.Lock()

    def bind(self, bindings: TranscriberBindings) -> None:
        """Attach UI/runtime callbacks."""
        self._bindings = bindings

    def set_callbacks(
        self,
        on_realtime_update: RealtimeCallback | None = None,
        on_realtime_stabilized: RealtimeCallback | None = None,
        on_batch_result: BatchCallback | None = None,
        on_error: ErrorCallback | None = None,
        **_kwargs,
    ) -> None:
        """Backward-compatible callback wiring wrapper."""
        self.bind(
            TranscriberBindings(
                on_realtime_update=on_realtime_update,
                on_realtime_stabilized=on_realtime_stabilized,
                on_batch_result=on_batch_result,
                on_error=on_error,
            )
        )

    def prepare(self) -> None:
        """Load runtime prerequisites and the realtime model."""
        self._vad = _SileroVAD()
        self._model_manager.prepare()
        self._realtime_model = self._model_manager.get_live_model()
        self._model = self._realtime_model
        self._ready = True

    def begin_session(self) -> None:
        """Start the worker thread for a recording session."""
        if not self._ready:
            msg = "Transcriber is not prepared."
            raise RuntimeError(msg)
        if self._worker is not None and self._worker.is_alive():
            return
        self._stop_event.clear()
        self._drain_queue()
        self._audio_drop_reported = False
        self._worker = threading.Thread(
            target=self._run_worker,
            daemon=True,
            name="transcriber",
        )
        self._worker.start()

    def start(self) -> None:
        """Backward-compatible session start wrapper."""
        if not self._ready:
            return
        self.begin_session()

    def end_session(self) -> None:
        """Stop the worker thread for the current recording session."""
        self._stop_event.set()
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            log.warning(
                "Transcriber queue full during shutdown; waiting for worker exit"
            )

    def stop(self) -> None:
        """Backward-compatible session stop wrapper."""
        self.end_session()

    def shutdown(self, timeout: float | None = 3) -> None:
        """Stop worker and release runtime resources."""
        self.end_session()
        worker_alive = False
        if self._worker is not None:
            self._worker.join(timeout=timeout)
            worker_alive = self._worker.is_alive()
            if worker_alive:
                self._emit_error(
                    "shutdown",
                    (
                        "Transcriber worker did not exit cleanly before shutdown "
                        "timed out."
                    ),
                )
            else:
                self._worker = None
        self._ready = False
        if worker_alive:
            return
        if self._vad is not None:
            self._vad.close()
        self._vad = None
        self._realtime_model = None
        self._model = None
        self._model_manager.release_models()

    def accept_audio(self, chunk: np.ndarray) -> None:
        """Accept audio from the recorder callback without blocking."""
        if not self._ready or self._stop_event.is_set():
            return
        try:
            self._queue.put_nowait(chunk.copy())
        except queue.Full:
            if not self._audio_drop_reported:
                self._audio_drop_reported = True
                self._emit_error(
                    "audio",
                    "Audio processing is falling behind; dropping microphone audio.",
                )

    def feed_audio(self, chunk: np.ndarray) -> None:
        """Backward-compatible audio ingestion wrapper."""
        self.accept_audio(chunk)

    def transcribe_batch(
        self,
        audio: np.ndarray,
        batch_elapsed: int,
        *,
        emit_callback: bool = True,
    ) -> str | None:
        """Run the accurate batch model on a drained recorder buffer.

        Returns the transcribed text, empty string if nothing was recognized,
        or None on error. The normal executor-driven path still emits the
        callback so the UI updates via call_from_thread.
        """
        try:
            with self._batch_lock:
                model = self._model_manager.get_batch_model()
                segments, _ = model.transcribe(
                    audio,
                    language=config.LANGUAGE,
                    beam_size=config.BEAM_SIZE,
                    vad_filter=True,
                    condition_on_previous_text=config.CONDITION_ON_PREVIOUS_TEXT,
                )
            text = " ".join(seg.text.strip() for seg in segments).strip()
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
        """True when the realtime worker thread exists and has not been joined."""
        return self._worker is not None

    def _drain_queue(self) -> None:
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break

    def _emit_error(self, source: str, message: str) -> None:
        log.error("%s: %s", source, message)
        if self._bindings.on_error is not None:
            self._bindings.on_error(source, message)

    def _run_worker(self) -> None:
        """Main loop: dequeue audio, run VAD, then transcribe."""
        assert self._vad is not None
        model = self._realtime_model or self._model
        assert model is not None

        vad = self._vad
        state = _VadState.SILENCE
        residual = np.array([], dtype=np.float32)

        pre_buf_max = int(config.VAD_PRE_BUFFER_SECONDS * config.SAMPLE_RATE)
        pre_buffer: deque[np.ndarray] = deque()
        pre_buffer_samples = 0

        speech_audio: list[np.ndarray] = []
        speech_samples = 0
        silence_samples = 0
        last_transcribe_samples = 0
        transcribe_interval = int(config.REALTIME_PROCESSING_PAUSE * config.SAMPLE_RATE)
        min_speech = int(config.VAD_MIN_SPEECH_SECONDS * config.SAMPLE_RATE)
        silence_threshold = int(config.VAD_SILENCE_SECONDS * config.SAMPLE_RATE)
        max_speech = int(config.REALTIME_MAX_SPEECH * config.SAMPLE_RATE)
        chunk_size = config.VAD_CHUNK_SAMPLES

        while True:
            if self._stop_event.is_set() and self._queue.empty():
                if state == _VadState.SPEECH and speech_samples >= min_speech:
                    self._transcribe_and_notify(
                        speech_audio,
                        model=model,
                        stabilized=True,
                    )
                break

            try:
                raw = self._queue.get(timeout=0.1)
            except queue.Empty:
                continue

            if raw is None:
                if state == _VadState.SPEECH and speech_samples >= min_speech:
                    self._transcribe_and_notify(
                        speech_audio,
                        model=model,
                        stabilized=True,
                    )
                break

            if raw.dtype == np.int16:
                audio = raw.astype(np.float32).squeeze() / 32768.0
            else:
                audio = raw.squeeze().astype(np.float32)

            residual = np.concatenate([residual, audio])

            while len(residual) >= chunk_size:
                chunk = residual[:chunk_size]
                residual = residual[chunk_size:]

                try:
                    prob = vad(chunk)
                except Exception:
                    log.exception("VAD processing failed")
                    self._emit_error(
                        "vad",
                        (
                            "Voice activity detection failed. Retrying with a fresh "
                            "VAD state."
                        ),
                    )
                    vad.reset_states()
                    state = _VadState.SILENCE
                    pre_buffer.clear()
                    pre_buffer_samples = 0
                    speech_audio = []
                    speech_samples = 0
                    silence_samples = 0
                    last_transcribe_samples = 0
                    break

                pre_buffer.append(chunk)
                pre_buffer_samples += chunk_size
                while pre_buffer_samples > pre_buf_max:
                    removed = pre_buffer.popleft()
                    pre_buffer_samples -= len(removed)

                if state == _VadState.SILENCE:
                    if prob >= config.VAD_THRESHOLD:
                        state = _VadState.SPEECH
                        speech_audio = list(pre_buffer)
                        speech_samples = pre_buffer_samples
                        silence_samples = 0
                        last_transcribe_samples = 0
                        log.debug("VAD: speech start")
                    continue

                speech_audio.append(chunk)
                speech_samples += chunk_size

                if prob < config.VAD_NEG_THRESHOLD:
                    silence_samples += chunk_size
                else:
                    silence_samples = 0

                since_last = speech_samples - last_transcribe_samples
                if since_last >= transcribe_interval and speech_samples >= min_speech:
                    self._transcribe_and_notify(
                        speech_audio,
                        model=model,
                        stabilized=False,
                    )
                    last_transcribe_samples = speech_samples

                force_break = speech_samples >= max_speech
                if silence_samples >= silence_threshold or force_break:
                    if speech_samples >= min_speech:
                        self._transcribe_and_notify(
                            speech_audio,
                            model=model,
                            stabilized=True,
                        )
                    state = _VadState.SILENCE
                    speech_audio = []
                    speech_samples = 0
                    silence_samples = 0
                    last_transcribe_samples = 0
                    pre_buffer.clear()
                    pre_buffer_samples = 0
                    vad.reset_states()
                    log.debug(
                        "VAD: speech end%s",
                        " (forced)" if force_break else "",
                    )

    def _transcribe_and_notify(
        self,
        chunks: list[np.ndarray],
        *,
        model,
        stabilized: bool,
    ) -> None:
        """Transcribe accumulated audio and emit the correct callback."""
        if not chunks:
            return

        audio = np.concatenate(chunks)
        if not stabilized:
            max_samples = int(config.REALTIME_MAX_WINDOW * config.SAMPLE_RATE)
            if len(audio) > max_samples:
                audio = audio[-max_samples:]

        try:
            segments, _ = model.transcribe(
                audio,
                language=config.LANGUAGE,
                beam_size=config.BEAM_SIZE_REALTIME,
                vad_filter=False,
                condition_on_previous_text=config.CONDITION_ON_PREVIOUS_TEXT,
            )
            text = " ".join(seg.text.strip() for seg in segments).strip()
        except Exception:
            log.exception("Realtime transcription failed")
            self._emit_error(
                "realtime",
                "Realtime transcription failed. Live captions may pause until "
                "the next utterance.",
            )
            return

        if not text:
            return

        if stabilized and self._bindings.on_realtime_stabilized is not None:
            self._bindings.on_realtime_stabilized(text)
        elif not stabilized and self._bindings.on_realtime_update is not None:
            self._bindings.on_realtime_update(text)
