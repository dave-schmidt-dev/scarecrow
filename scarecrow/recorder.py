"""Audio capture — sounddevice InputStream writing to WAV via soundfile."""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path

import numpy as np
import sounddevice as sd
import soundfile as sf


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
        channels: int = 1,
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

        # Peak level for audio meter (updated in callback)
        self._peak_level: float = 0.0

    def _callback(
        self,
        indata: np.ndarray,
        _frames: int,
        _time,
        _status,
    ) -> None:
        """PortAudio callback — runs on a dedicated audio thread."""
        with self._lock:
            if not self._recording or self._sound_file is None:
                return
            if self._paused:
                silence = np.zeros_like(indata)
                self._sound_file.write(silence)
                self._peak_level = 0.0
            else:
                self._sound_file.write(indata)
                # Track peak level for audio meter
                peak = float(np.abs(indata).max()) / 32768.0
                self._peak_level = peak
                # Buffer for batch transcription
                with self._buffer_lock:
                    self._audio_chunks.append(indata.copy())
                # Feed to RealtimeSTT (or other consumer)
                if self._on_audio is not None:
                    self._on_audio(indata)

    def start(self) -> None:
        """Opens sounddevice InputStream with callback, opens SoundFile for writing."""
        if self._recording:
            return

        self._sound_file = sf.SoundFile(
            self._output_path,
            mode="w",
            samplerate=self._sample_rate,
            channels=self._channels,
            subtype="PCM_16",
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

        self._stream.start()

    def drain_buffer(self) -> np.ndarray | None:
        """Return accumulated audio since last drain, or None if empty.

        Audio is returned as float32 normalized to [-1, 1] at 16kHz
        — ready for Whisper with no resampling needed.
        """
        with self._buffer_lock:
            if not self._audio_chunks:
                return None
            chunks = self._audio_chunks.copy()
            self._audio_chunks.clear()

        combined = np.concatenate(chunks, axis=0).squeeze()
        # Convert int16 → float32 normalized for Whisper
        return combined.astype(np.float32) / 32768.0

    def pause(self) -> None:
        with self._lock:
            self._paused = True

    def resume(self) -> None:
        with self._lock:
            self._paused = False

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
        """Current peak audio level, 0.0 to 1.0."""
        return self._peak_level
