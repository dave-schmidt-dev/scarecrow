"""System audio capture via BlackHole — WAV recording + peak level only.

No VAD, no transcription buffer, no drain methods. This is a lightweight
companion to AudioRecorder that records system audio for archival and
future diarization (Phase 2).
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


def find_blackhole_device(name: str = "BlackHole") -> int | None:
    """Find an input device by name substring (case-insensitive).

    Returns the sounddevice device index, or None if no match found.
    """
    import sounddevice as sd

    for i, dev in enumerate(sd.query_devices()):
        if dev["max_input_channels"] > 0 and name.lower() in dev["name"].lower():
            return i
    return None


class SystemAudioCapture:
    """Lightweight audio capture for a named device. No VAD, no transcription.

    Writes all channels to a WAV file and exposes a decaying peak level
    for the InfoBar meter. The writer thread pattern mirrors AudioRecorder
    (sentinel protocol, SoundFile ownership by the writer thread).
    """

    def __init__(self, output_path: Path, *, device: int) -> None:
        import sounddevice as sd

        self._output_path = output_path
        self._device = device

        dev_info = sd.query_devices(device)
        self._sample_rate = int(dev_info["default_samplerate"])
        self._channels: int = dev_info["max_input_channels"]

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
        self._start_monotonic: float = 0.0

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
        """Stop recording, close stream and file."""
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

    # -- Private -----------------------------------------------------------

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

        data_copy = indata.copy()
        with contextlib.suppress(queue.Full):
            self._write_queue.put_nowait(("audio", data_copy))

        # Peak level across all channels
        peak = float(np.abs(indata.astype(np.int32)).max()) / 32768.0
        with self._lock:
            if peak > self._held_peak:
                self._held_peak = peak

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
