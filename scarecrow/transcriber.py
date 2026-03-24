"""Live transcription — Silero VAD + faster-whisper, no subprocesses."""

from __future__ import annotations

import contextlib
import logging
import queue
import threading
from collections import deque
from collections.abc import Callable
from enum import Enum, auto
from pathlib import Path

import numpy as np
import onnxruntime

from scarecrow import config

log = logging.getLogger(__name__)


# ------------------------------------------------------------------
# Silero VAD — pure numpy + ONNX, no torch
# ------------------------------------------------------------------


class _SileroVAD:
    """Silero VAD wrapper using ONNX runtime. No torch dependency."""

    def __init__(self) -> None:
        onnx_path = str(Path(__file__).parent / "models" / "silero_vad.onnx")
        opts = onnxruntime.SessionOptions()
        opts.inter_op_num_threads = 1
        opts.intra_op_num_threads = 1
        opts.log_severity_level = 3  # suppress ONNX warnings
        self._session = onnxruntime.InferenceSession(onnx_path, sess_options=opts)
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros((1, 64), dtype=np.float32)
        self._sr = np.array(16000, dtype=np.int64)

    def __call__(self, chunk: np.ndarray) -> float:
        """Run VAD on exactly 512 float32 samples. Returns probability."""
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


# ------------------------------------------------------------------
# VAD state machine
# ------------------------------------------------------------------


class _VadState(Enum):
    SILENCE = auto()
    SPEECH = auto()


# ------------------------------------------------------------------
# Transcriber
# ------------------------------------------------------------------


class Transcriber:
    """Live transcription with Silero VAD + faster-whisper.

    Single-process, single-thread worker. No torch, no subprocesses.

    Call prepare() before the Textual app starts, then start() to begin
    processing audio. Feed audio via feed_audio() from the audio callback.
    """

    def __init__(
        self,
        on_realtime_update: Callable[[str], None] | None = None,
        on_realtime_stabilized: Callable[[str], None] | None = None,
    ) -> None:
        self._on_realtime_update_cb = on_realtime_update
        self._on_realtime_stabilized_cb = on_realtime_stabilized
        self._vad: _SileroVAD | None = None
        self._model = None
        self._queue: queue.Queue[np.ndarray | None] = queue.Queue(maxsize=200)
        self._worker: threading.Thread | None = None
        self._ready = False

    def set_callbacks(
        self,
        on_realtime_update: Callable[[str], None] | None = None,
        on_realtime_stabilized: Callable[[str], None] | None = None,
        **_kwargs,
    ) -> None:
        """Wire up callbacks (e.g. once the App exists)."""
        self._on_realtime_update_cb = on_realtime_update
        self._on_realtime_stabilized_cb = on_realtime_stabilized

    def prepare(self) -> None:
        """Load VAD model and Whisper model. Call before app.run()."""
        from faster_whisper import WhisperModel

        self._vad = _SileroVAD()
        self._model = WhisperModel(
            config.REALTIME_MODEL,
            device="cpu",
            compute_type="int8",
        )
        self._ready = True

    def start(self) -> None:
        """Start the VAD + transcription worker thread."""
        if not self._ready:
            return
        if self._worker is not None and self._worker.is_alive():
            return
        # Drain any stale data from previous session
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
        self._worker = threading.Thread(
            target=self._run_worker, daemon=True, name="transcriber"
        )
        self._worker.start()

    def stop(self) -> None:
        """Signal the worker to stop."""
        with contextlib.suppress(queue.Full):
            self._queue.put_nowait(None)

    def shutdown(self) -> None:
        """Stop worker and clean up."""
        self.stop()
        if self._worker is not None:
            self._worker.join(timeout=3)
            self._worker = None

    def feed_audio(self, chunk: np.ndarray) -> None:
        """Feed audio from the audio callback. Must not block."""
        if not self._ready:
            return
        with contextlib.suppress(queue.Full):
            self._queue.put_nowait(chunk.copy())

    @property
    def is_ready(self) -> bool:
        return self._ready

    # ------------------------------------------------------------------
    # Worker thread
    # ------------------------------------------------------------------

    def _run_worker(self) -> None:
        """Main loop: dequeue audio → VAD → transcribe."""
        assert self._vad is not None
        assert self._model is not None

        vad = self._vad
        state = _VadState.SILENCE
        residual = np.array([], dtype=np.float32)

        # Pre-recording buffer: 1.0s of audio before speech detected
        pre_buf_max = int(config.VAD_PRE_BUFFER_SECONDS * config.SAMPLE_RATE)
        pre_buffer: deque[np.ndarray] = deque()
        pre_buffer_samples = 0

        # Speech accumulation
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
            try:
                raw = self._queue.get(timeout=0.1)
            except queue.Empty:
                continue

            if raw is None:
                # Poison pill — flush any remaining speech
                if state == _VadState.SPEECH and speech_samples >= min_speech:
                    self._transcribe_and_notify(speech_audio, stabilized=True)
                break

            # Convert int16 → float32 normalized
            if raw.dtype == np.int16:
                audio = raw.astype(np.float32).squeeze() / 32768.0
            else:
                audio = raw.squeeze().astype(np.float32)

            # Append to residual and process in 512-sample chunks
            residual = np.concatenate([residual, audio])

            while len(residual) >= chunk_size:
                chunk = residual[:chunk_size]
                residual = residual[chunk_size:]

                prob = vad(chunk)

                # Update pre-buffer (always, regardless of state)
                pre_buffer.append(chunk)
                pre_buffer_samples += chunk_size
                while pre_buffer_samples > pre_buf_max:
                    removed = pre_buffer.popleft()
                    pre_buffer_samples -= len(removed)

                if state == _VadState.SILENCE:
                    if prob >= config.VAD_THRESHOLD:
                        # Speech detected — start accumulating
                        state = _VadState.SPEECH
                        speech_audio = list(pre_buffer)
                        speech_samples = pre_buffer_samples
                        silence_samples = 0
                        last_transcribe_samples = 0
                        log.debug("VAD: speech start")

                elif state == _VadState.SPEECH:
                    speech_audio.append(chunk)
                    speech_samples += chunk_size

                    if prob < config.VAD_NEG_THRESHOLD:
                        silence_samples += chunk_size
                    else:
                        silence_samples = 0

                    # Periodic transcription during speech
                    since_last = speech_samples - last_transcribe_samples
                    if (
                        since_last >= transcribe_interval
                        and speech_samples >= min_speech
                    ):
                        self._transcribe_and_notify(speech_audio, stabilized=False)
                        last_transcribe_samples = speech_samples

                    # Utterance end: enough silence OR max duration reached
                    force_break = speech_samples >= max_speech
                    if silence_samples >= silence_threshold or force_break:
                        if speech_samples >= min_speech:
                            self._transcribe_and_notify(speech_audio, stabilized=True)
                        state = _VadState.SILENCE
                        speech_audio = []
                        speech_samples = 0
                        silence_samples = 0
                        last_transcribe_samples = 0
                        vad.reset_states()
                        log.debug(
                            "VAD: speech end%s",
                            " (forced)" if force_break else "",
                        )

    def _transcribe_and_notify(
        self,
        chunks: list[np.ndarray],
        *,
        stabilized: bool,
    ) -> None:
        """Transcribe accumulated audio and fire callback."""
        if not chunks or self._model is None:
            return
        audio = np.concatenate(chunks)

        # For live updates, only transcribe the tail to cap CPU usage.
        # For stabilized (utterance end), transcribe the full buffer.
        if not stabilized:
            max_samples = int(config.REALTIME_MAX_WINDOW * config.SAMPLE_RATE)
            if len(audio) > max_samples:
                audio = audio[-max_samples:]

        try:
            segments, _ = self._model.transcribe(
                audio,
                language=config.LANGUAGE,
                beam_size=config.BEAM_SIZE_REALTIME,
                vad_filter=False,
                condition_on_previous_text=False,
            )
            text = " ".join(seg.text.strip() for seg in segments).strip()
        except Exception:
            log.exception("Realtime transcription failed")
            return

        if not text:
            return

        if stabilized and self._on_realtime_stabilized_cb is not None:
            self._on_realtime_stabilized_cb(text)
        elif not stabilized and self._on_realtime_update_cb is not None:
            self._on_realtime_update_cb(text)
