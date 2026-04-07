"""System audio capture — WAV recording + transcription buffer.

Captures system audio for archival (WAV) and exposes drain methods for
VAD-driven transcription, mirroring AudioRecorder's buffer interface.
Stereo input is downmixed to mono for Parakeet.

# TODO: extract shared AudioBuffer with recorder.py
"""

from __future__ import annotations

import contextlib
import logging
import queue
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    import sounddevice as sd
    import soundfile as sf

log = logging.getLogger(__name__)


def find_system_audio_device(name: str = "BlackHole") -> int | None:
    """Find an input device by name substring (case-insensitive).

    Returns the sounddevice device index, or None if no match found.
    """
    import sounddevice as sd

    for i, dev in enumerate(sd.query_devices()):
        if dev["max_input_channels"] > 0 and name.lower() in dev["name"].lower():
            return i
    return None


class SystemAudioCapture:
    """Audio capture for a named device with transcription buffer.

    Writes all channels to a WAV file and exposes drain methods for
    VAD-driven transcription. Stereo is downmixed to mono for the
    transcription buffer. The writer thread pattern mirrors AudioRecorder
    (sentinel protocol, SoundFile ownership by the writer thread).
    """

    def __init__(self, output_path: Path, *, device: int) -> None:
        import sounddevice as sd

        self._output_path = output_path
        self._device = device

        dev_info = sd.query_devices(device)
        self._sample_rate = int(dev_info["default_samplerate"])
        self._channels: int = dev_info["max_input_channels"]
        if self._channels > 2:
            log.warning(
                "System audio device has %d channels (expected 1-2); "
                "downmix to mono may be lossy",
                self._channels,
            )

        self._stream: sd.InputStream | None = None
        self._sound_file: sf.SoundFile | None = None
        self._write_queue: queue.Queue[tuple[str, np.ndarray] | None] = queue.Queue(
            maxsize=600
        )
        self._writer_thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._recording = False
        self._paused = False
        self._held_peak: float = 0.0
        self._peak_decay: float = 0.15
        self._gain: float = 1.0
        self._start_monotonic: float = 0.0

        # Transcription buffer — mono int16 chunks + per-chunk RMS
        self._audio_chunks: list[np.ndarray] = []
        self._chunk_energies: list[float] = []
        self._buffer_lock = threading.Lock()
        self._stt_sample_rate: int = 16000

    def start(self) -> None:
        """Open stream on the configured device and start recording."""
        import sounddevice as sd
        import soundfile as sf

        if self._recording:
            return

        self._sound_file = sf.SoundFile(
            self._output_path,
            mode="w",
            samplerate=self._sample_rate,
            channels=self._channels,
            subtype="PCM_16",
        )

        while not self._write_queue.empty():
            try:
                self._write_queue.get_nowait()
            except queue.Empty:
                break

        # Clear transcription buffer from any prior run
        with self._buffer_lock:
            self._audio_chunks.clear()
            self._chunk_energies.clear()

        self._writer_thread = threading.Thread(
            target=self._writer_loop,
            name="sys-wav-writer",
            daemon=True,
        )
        self._writer_thread.start()

        self._stream = sd.InputStream(
            samplerate=self._sample_rate,
            channels=self._channels,
            dtype="int16",
            device=self._device,
            callback=self._callback,
        )

        with self._lock:
            self._recording = True
            self._paused = False

        self._start_monotonic = time.monotonic()
        self._stream.start()

    def stop(self) -> Path:
        """Stop recording, close stream and file.

        NOTE: Does NOT clear _audio_chunks — shutdown code may still need
        to drain the transcription buffer after stopping the stream.
        """
        with self._lock:
            self._recording = False

        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

        if self._writer_thread is not None:
            self._write_queue.put(None)
            self._writer_thread.join(timeout=5.0)
            self._writer_thread = None

        # Safety net: close file if writer thread didn't
        if self._sound_file is not None:
            self._sound_file.close()
            self._sound_file = None

        return self._output_path

    def pause(self) -> None:
        with self._lock:
            self._paused = True
        if self._stream is not None:
            self._stream.stop()

    def resume(self) -> None:
        with self._lock:
            self._paused = False
        if self._stream is not None:
            self._stream.start()

    @property
    def peak_level(self) -> float:
        """Held peak audio level, 0.0 to 1.0. Decays on each read."""
        with self._lock:
            level = self._held_peak
            self._held_peak = max(0.0, self._held_peak - self._peak_decay)
            return level

    @property
    def is_recording(self) -> bool:
        return self._recording

    # -- Transcription buffer -------------------------------------------------

    def _finalize_audio(self, chunks: list[np.ndarray]) -> np.ndarray:
        """Concatenate mono int16 chunks → float32, downsampled to STT rate."""
        combined = np.concatenate(chunks, axis=0).reshape(-1)
        audio = combined.astype(np.float32) / 32768.0
        if self._sample_rate != self._stt_sample_rate:
            duration = len(audio) / self._sample_rate
            new_length = int(duration * self._stt_sample_rate)
            old_times = np.linspace(0, duration, num=len(audio), endpoint=False)
            new_times = np.linspace(0, duration, num=new_length, endpoint=False)
            audio = np.interp(new_times, old_times, audio).astype(np.float32)
        return audio

    def drain_buffer(self) -> np.ndarray | None:
        """Return accumulated audio since last drain, or None if empty.

        Audio is returned as float32 normalized to [-1, 1] at 16kHz.
        """
        with self._buffer_lock:
            if not self._audio_chunks:
                return None
            chunks = self._audio_chunks.copy()
            self._audio_chunks.clear()
            self._chunk_energies.clear()

        return self._finalize_audio(chunks)

    def drain_to_silence(
        self,
        silence_threshold: float = 0.01,
        min_silence_ms: int = 750,
        max_buffer_seconds: float = 30,
        min_buffer_seconds: float = 0.5,
    ) -> tuple[np.ndarray, list[float]] | None:
        """Drain audio up to the most recent silence boundary.

        Returns ``(audio, chunk_energies)`` or ``None`` if too short.
        Thresholds are passed as params (not read from config) since
        sys audio may use different values than mic.
        """
        with self._buffer_lock:
            if not self._audio_chunks:
                return None

            n_chunks = len(self._audio_chunks)
            samples_per_chunk = len(self._audio_chunks[0])
            total_samples = sum(len(c) for c in self._audio_chunks)
            total_seconds = total_samples / self._sample_rate

            if total_seconds < min_buffer_seconds:
                return None

            min_silence_samples = int(self._sample_rate * min_silence_ms / 1000)
            _denom = max(samples_per_chunk, 1)
            min_silent_chunks = max(1, -(-min_silence_samples // _denom))

            silence_end = None
            consecutive_silent = 0
            for i in range(n_chunks - 1, -1, -1):
                if self._chunk_energies[i] < silence_threshold:
                    consecutive_silent += 1
                    if consecutive_silent >= min_silent_chunks:
                        silence_end = i
                        break
                else:
                    consecutive_silent = 0

            if silence_end is not None:
                if silence_end == 0:
                    self._audio_chunks.clear()
                    self._chunk_energies.clear()
                    return None
                drain_end = min(silence_end + consecutive_silent, n_chunks)
                chunks = self._audio_chunks[:drain_end]
                energies = list(self._chunk_energies[:drain_end])
                self._audio_chunks = self._audio_chunks[drain_end:]
                self._chunk_energies = self._chunk_energies[drain_end:]
            elif total_seconds >= max_buffer_seconds:
                chunks = self._audio_chunks.copy()
                energies = list(self._chunk_energies)
                self._audio_chunks.clear()
                self._chunk_energies.clear()
            else:
                return None

        return self._finalize_audio(chunks), energies

    @property
    def buffer_seconds(self) -> float:
        """Current buffer duration in seconds."""
        with self._buffer_lock:
            if not self._audio_chunks:
                return 0.0
            return sum(len(c) for c in self._audio_chunks) / self._sample_rate

    # -- Private (audio callback + writer) ------------------------------------

    def _callback(
        self,
        indata: np.ndarray,
        _frames: int,
        _time,
        status,
    ) -> None:
        """PortAudio callback — runs on a dedicated audio thread."""
        try:
            self._callback_inner(indata, status)
        except Exception:
            log.exception("Unexpected error in sys audio callback")

    def _callback_inner(self, indata: np.ndarray, status) -> None:
        if status:
            log.warning("sys audio device status: %s", status)

        with self._lock:
            if not self._recording:
                return
            if self._paused:
                silence = np.zeros_like(indata)
                with contextlib.suppress(queue.Full):
                    self._write_queue.put_nowait(("silence", silence))
                return

        # CRITICAL: indata is a PortAudio buffer view — copy immediately.
        # raw holds pre-gain audio and is written to disk so that replaying
        # the FLAC produces the same signal levels as the live session.
        raw = indata.copy()
        with contextlib.suppress(queue.Full):
            self._write_queue.put_nowait(("audio", raw))

        # Apply gain to a separate copy used only for the transcription
        # buffer and peak meter.
        gain = self._gain
        if gain != 1.0:
            data_copy = np.clip(raw.astype(np.int32) * gain, -32768, 32767).astype(
                np.int16
            )
        else:
            data_copy = raw

        # Peak level across all channels (post-gain so meter
        # reflects what Parakeet hears and responds to gain changes)
        peak = float(np.abs(data_copy.astype(np.int32)).max()) / 32768.0
        with self._lock:
            if peak > self._held_peak:
                self._held_peak = peak

        # Downmix to mono for transcription buffer (post-gain)
        if self._channels > 1:
            mono = (data_copy.astype(np.int32).mean(axis=1)).astype(np.int16)
        else:
            mono = data_copy.reshape(-1)

        # RMS for VAD uses pre-gain audio so thresholds stay calibrated
        # regardless of gain setting (consistent with mic recorder)
        if self._channels > 1:
            raw_mono = (raw.astype(np.int32).mean(axis=1)).astype(np.int16)
        else:
            raw_mono = raw.reshape(-1)
        rms = float(np.sqrt(np.mean((raw_mono.astype(np.float32) / 32768.0) ** 2)))

        with self._buffer_lock:
            self._audio_chunks.append(mono)
            self._chunk_energies.append(rms)

    def _writer_loop(self) -> None:
        """Dedicated thread: pulls audio from queue, writes to SoundFile."""
        while True:
            item = self._write_queue.get()
            if item is None:
                while True:
                    try:
                        remaining = self._write_queue.get_nowait()
                    except queue.Empty:
                        break
                    if remaining is not None:
                        self._write_chunk(remaining[1])
                break
            _tag, data = item
            self._write_chunk(data)

        if self._sound_file is not None:
            self._sound_file.close()
            self._sound_file = None

    def _write_chunk(self, data: np.ndarray) -> None:
        try:
            if self._sound_file is not None:
                self._sound_file.write(data)
        except OSError:
            log.exception("Failed to write sys audio to file")
