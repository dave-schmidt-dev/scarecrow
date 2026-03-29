"""Audio capture — sounddevice InputStream writing to WAV via soundfile."""

from __future__ import annotations

import contextlib
import logging
import queue
import threading
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from scarecrow import config

if TYPE_CHECKING:
    import sounddevice as sd
    import soundfile as sf


class AudioRecorder:
    """Records audio from microphone to WAV file.

    Also maintains an in-memory buffer of audio for VAD-based batch
    transcription. Call drain_buffer() or drain_to_silence() to get
    accumulated audio since the last drain.
    """

    def __init__(
        self,
        output_path: Path,
        sample_rate: int = 16000,
        channels: int = config.CHANNELS,
    ) -> None:
        self._output_path = output_path
        self._sample_rate = sample_rate
        self._channels = channels

        self._stream: sd.InputStream | None = None
        self._sound_file: sf.SoundFile | None = None

        self._recording = False
        self._paused = False
        self._lock = threading.Lock()

        # In-memory buffer for batch transcription
        self._audio_chunks: list[np.ndarray] = []
        self._chunk_energies: list[float] = []  # RMS per chunk, for VAD
        self._buffer_lock = threading.Lock()
        # Peak level for audio meter (updated in callback)
        self._peak_level: float = 0.0
        self._held_peak: float = 0.0
        self._peak_decay: float = 0.15  # decay per read

        # Disk-write and status warning state (polled by app.py)
        self._disk_write_failed: bool = False
        self._last_warning: str | None = None
        self._last_status_warning: str = ""

        # Writer thread: pulls audio from queue, writes to SoundFile
        self._write_queue: queue.Queue[tuple[str, np.ndarray] | None] = queue.Queue(
            maxsize=config.WRITER_QUEUE_SIZE,
        )
        self._writer_thread: threading.Thread | None = None

    def _callback(
        self,
        indata: np.ndarray,
        _frames: int,
        _time,
        status,
    ) -> None:
        """PortAudio callback — runs on a dedicated audio thread."""
        try:
            self._callback_inner(indata, _frames, _time, status)
        except Exception:
            logging.getLogger(__name__).exception("Unexpected error in audio callback")

    def _callback_inner(self, indata, _frames, _time, status):
        """Inner body of PortAudio callback (separated for safety-net wrapping)."""
        if status:
            status_str = str(status).lower()
            if "input overflow" in status_str:
                warning_str = "Audio input overflow"
            else:
                warning_str = f"Audio device error: {status}"
            if warning_str != self._last_status_warning:
                self._last_status_warning = warning_str
                self._last_warning = warning_str
                logging.getLogger(__name__).warning("sounddevice status: %s", status)

        with self._lock:
            if not self._recording:
                return
            if self._paused:
                silence = np.zeros_like(indata)
                with contextlib.suppress(queue.Full):
                    self._write_queue.put_nowait(("silence", silence))
                self._peak_level = 0.0
            else:
                # Copy once, share between queue and transcription buffer
                data_copy = indata.copy()
                try:
                    self._write_queue.put_nowait(("audio", data_copy))
                except queue.Full:
                    if not self._disk_write_failed:
                        self._disk_write_failed = True
                        self._last_warning = (
                            "Audio write queue full \u2014 disk too slow"
                        )
                        logging.getLogger(__name__).error(
                            "Write queue full, dropping audio frame"
                        )
                # Track peak level for audio meter
                peak = float(np.abs(indata.astype(np.int32)).max()) / 32768.0
                self._peak_level = peak
                if peak > self._held_peak:
                    self._held_peak = peak
                # Compute RMS for VAD
                rms = float(
                    np.sqrt(np.mean((indata.astype(np.float32) / 32768.0) ** 2))
                )
                # Buffer for batch transcription (shares the same copy)
                with self._buffer_lock:
                    self._audio_chunks.append(data_copy)
                    self._chunk_energies.append(rms)

    def _writer_loop(self) -> None:
        """Dedicated thread: pulls audio from queue, writes to SoundFile."""
        log = logging.getLogger(__name__)
        while True:
            item = self._write_queue.get()
            if item is None:
                # Sentinel: drain remaining items, then close file
                while True:
                    try:
                        remaining = self._write_queue.get_nowait()
                    except queue.Empty:
                        break
                    if remaining is not None:
                        self._write_chunk(remaining[1], log)
                break
            _tag, data = item
            self._write_chunk(data, log)

        # Writer thread owns file closure
        if self._sound_file is not None:
            self._sound_file.close()
            self._sound_file = None

    def _write_chunk(self, data: np.ndarray, log: logging.Logger) -> None:
        """Write a single audio chunk to the SoundFile."""
        try:
            if self._sound_file is not None:
                self._sound_file.write(data)
        except OSError:
            if not self._disk_write_failed:
                self._disk_write_failed = True
                self._last_warning = "Audio file write failed \u2014 disk may be full"
                log.exception("Failed to write audio to file")

    def start(self) -> None:
        """Opens sounddevice InputStream with callback, opens SoundFile for writing."""
        import sounddevice as sd
        import soundfile as sf

        if self._recording:
            return

        self._sound_file = sf.SoundFile(
            self._output_path,
            mode="w",
            samplerate=self._sample_rate,
            channels=self._channels,
            subtype=config.SUBTYPE,
        )

        # Clear any stale queue items and start the writer thread
        while not self._write_queue.empty():
            try:
                self._write_queue.get_nowait()
            except queue.Empty:
                break

        self._writer_thread = threading.Thread(
            target=self._writer_loop,
            name="wav-writer",
            daemon=True,
        )
        self._writer_thread.start()

        self._stream = sd.InputStream(
            samplerate=self._sample_rate,
            channels=self._channels,
            dtype="int16",
            callback=self._callback,
        )

        with self._lock:
            self._recording = True
            self._paused = False

        with self._buffer_lock:
            self._audio_chunks.clear()
            self._chunk_energies.clear()
        self._stream.start()

    def _finalize_audio(self, chunks: list[np.ndarray]) -> np.ndarray:
        """Concatenate int16 chunks → float32."""
        combined = np.concatenate(chunks, axis=0).squeeze()
        return combined.astype(np.float32) / 32768.0

    def drain_buffer(self) -> np.ndarray | None:
        """Return accumulated audio since last drain, or None if empty.

        Audio is returned as float32 normalized to [-1, 1] at 16kHz
        — ready for Parakeet with no resampling needed.
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
        silence_threshold: float = config.VAD_SILENCE_THRESHOLD,
        min_silence_ms: int = config.VAD_MIN_SILENCE_MS,
        max_buffer_seconds: float = config.VAD_MAX_BUFFER_SECONDS,
    ) -> np.ndarray | None:
        """Drain audio up to the most recent silence boundary.

        Scans chunk energies from the end to find a run of consecutive
        silent blocks >= min_silence_ms. If found, drains up to the start
        of that silence. If no silence is found and the buffer exceeds
        max_buffer_seconds, does a hard drain of everything.

        Returns None if the buffer is too short to act on.
        """
        with self._buffer_lock:
            if not self._audio_chunks:
                return None

            n_chunks = len(self._audio_chunks)
            # Estimate samples per chunk from first chunk
            samples_per_chunk = len(self._audio_chunks[0])
            total_samples = sum(len(c) for c in self._audio_chunks)
            total_seconds = total_samples / self._sample_rate

            # Need at least 0.5s of audio before we bother looking
            if total_seconds < 0.5:
                return None

            # How many consecutive silent chunks needed
            min_silence_samples = int(self._sample_rate * min_silence_ms / 1000)
            _denom = max(samples_per_chunk, 1)
            min_silent_chunks = max(1, -(-min_silence_samples // _denom))

            # Scan from end backward for a silence run
            silence_end = None
            consecutive_silent = 0
            for i in range(n_chunks - 1, -1, -1):
                if self._chunk_energies[i] < silence_threshold:
                    consecutive_silent += 1
                    if consecutive_silent >= min_silent_chunks:
                        # Drain up to the start of this silence run
                        silence_start = i
                        silence_end = silence_start
                        break
                else:
                    consecutive_silent = 0

            if silence_end is not None:
                if silence_end == 0:
                    # Buffer starts with silence — no speech to drain.
                    # Discard the silent chunks to prevent unbounded growth.
                    self._audio_chunks.clear()
                    self._chunk_energies.clear()
                    return None
                # Drain through the silence (include silent chunks so
                # we don't clip words that fade into the silence gap)
                drain_end = min(silence_end + consecutive_silent, n_chunks)
                chunks = self._audio_chunks[:drain_end]
                self._audio_chunks = self._audio_chunks[drain_end:]
                self._chunk_energies = self._chunk_energies[drain_end:]
            elif total_seconds >= max_buffer_seconds:
                # Hard drain — continuous speech exceeded max
                chunks = self._audio_chunks.copy()
                self._audio_chunks.clear()
                self._chunk_energies.clear()
            else:
                # Not enough silence yet, keep accumulating
                return None

        return self._finalize_audio(chunks)

    @property
    def buffer_seconds(self) -> float:
        """Current buffer duration in seconds."""
        with self._buffer_lock:
            if not self._audio_chunks:
                return 0.0
            return sum(len(c) for c in self._audio_chunks) / self._sample_rate

    def pause(self) -> None:
        with self._lock:
            self._paused = True
        # Stop the stream to release the microphone
        if self._stream is not None:
            self._stream.stop()

    def resume(self) -> None:
        with self._lock:
            self._paused = False
        # Restart the stream to re-acquire the microphone
        if self._stream is not None:
            self._stream.start()

    def stop(self) -> Path:
        with self._lock:
            self._recording = False

        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

        # Signal writer thread to flush and exit
        if self._writer_thread is not None:
            self._write_queue.put(None)
            self._writer_thread.join(timeout=5.0)
            self._writer_thread = None

        # Safety net: close file if writer thread didn't (e.g., it hung)
        if self._sound_file is not None:
            self._sound_file.close()
            self._sound_file = None

        return self._output_path

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    @property
    def is_recording(self) -> bool:
        with self._lock:
            return self._recording

    @property
    def is_paused(self) -> bool:
        with self._lock:
            return self._paused

    @property
    def peak_level(self) -> float:
        """Held peak audio level, 0.0 to 1.0. Decays on each read."""
        with self._lock:
            level = self._held_peak
            self._held_peak = max(0.0, self._held_peak - self._peak_decay)
            return level
