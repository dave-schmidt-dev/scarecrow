"""Audio capture — sounddevice InputStream writing to WAV via soundfile."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path

import numpy as np
import sounddevice as sd
import soundfile as sf

from scarecrow import config


class AudioRecorder:
    """Records audio from microphone to WAV file.

    Also maintains an in-memory buffer of audio for batch transcription
    and optionally feeds audio to a callback (e.g. RealtimeSTT.feed_audio).
    Call drain_buffer() to get accumulated audio since the last drain.
    """

    def __init__(
        self,
        output_path: Path,
        sample_rate: int = 16000,
        channels: int = config.CHANNELS,
        on_audio: Callable[[np.ndarray], None] | None = None,
    ) -> None:
        self._output_path = output_path
        self._sample_rate = sample_rate
        self._channels = channels
        self._on_audio = on_audio

        self._stream: sd.InputStream | None = None
        self._sound_file: sf.SoundFile | None = None

        self._recording = False
        self._paused = False
        self._lock = threading.Lock()

        # In-memory buffer for batch transcription
        self._audio_chunks: list[np.ndarray] = []
        self._buffer_lock = threading.Lock()
        self._overlap_tail: np.ndarray | None = None
        self._overlap_samples = sample_rate // 2  # 500ms overlap

        # Peak level for audio meter (updated in callback)
        self._peak_level: float = 0.0
        self._held_peak: float = 0.0
        self._peak_decay: float = 0.15  # decay per read

        # Disk-write and status warning state (polled by app.py)
        self._disk_write_failed: bool = False
        self._last_warning: str | None = None
        self._last_status_warning: str = ""

    def _callback(
        self,
        indata: np.ndarray,
        _frames: int,
        _time,
        status,
    ) -> None:
        """PortAudio callback — runs on a dedicated audio thread."""
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
            if not self._recording or self._sound_file is None:
                return
            if self._paused:
                silence = np.zeros_like(indata)
                try:
                    self._sound_file.write(silence)
                except OSError:
                    if not self._disk_write_failed:
                        self._disk_write_failed = True
                        self._last_warning = (
                            "Audio file write failed \u2014 disk may be full"
                        )
                        logging.getLogger(__name__).exception(
                            "Failed to write silence to audio file"
                        )
                self._peak_level = 0.0
            else:
                try:
                    self._sound_file.write(indata)
                except OSError:
                    if not self._disk_write_failed:
                        self._disk_write_failed = True
                        self._last_warning = (
                            "Audio file write failed \u2014 disk may be full"
                        )
                        logging.getLogger(__name__).exception(
                            "Failed to write audio to file"
                        )
                # Track peak level for audio meter
                peak = float(np.abs(indata.astype(np.int32)).max()) / 32768.0
                self._peak_level = peak
                if peak > self._held_peak:
                    self._held_peak = peak
                # Buffer for batch transcription
                with self._buffer_lock:
                    self._audio_chunks.append(indata.copy())
                # Feed to RealtimeSTT (or other consumer)
                if self._on_audio is not None:
                    try:
                        self._on_audio(indata)
                    except Exception:
                        logging.getLogger(__name__).exception(
                            "Audio feed callback failed; live transcription may stop"
                        )

    def start(self) -> None:
        """Opens sounddevice InputStream with callback, opens SoundFile for writing."""
        if self._recording:
            return

        self._sound_file = sf.SoundFile(
            self._output_path,
            mode="w",
            samplerate=self._sample_rate,
            channels=self._channels,
            subtype=config.SUBTYPE,
        )

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
            self._overlap_tail = None

        self._stream.start()

    def drain_buffer(self) -> np.ndarray | None:
        """Return accumulated audio since last drain, or None if empty.

        Audio is returned as float32 normalized to [-1, 1] at 16kHz
        — ready for Whisper with no resampling needed.

        A 500ms overlap from the previous drain is prepended so
        Whisper has context at chunk boundaries (avoids dropped words).
        """
        with self._buffer_lock:
            if not self._audio_chunks:
                return None
            chunks = self._audio_chunks.copy()
            self._audio_chunks.clear()

        combined = np.concatenate(chunks, axis=0).squeeze()
        audio = combined.astype(np.float32) / 32768.0

        if self._overlap_tail is not None:
            audio = np.concatenate([self._overlap_tail, audio])

        if len(audio) > self._overlap_samples:
            self._overlap_tail = audio[-self._overlap_samples :]
        else:
            self._overlap_tail = audio.copy()

        return audio

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
