"""Audio capture — sounddevice InputStream writing to WAV via soundfile."""

import threading
from pathlib import Path

import numpy as np
import sounddevice as sd
import soundfile as sf


class AudioRecorder:
    """Records audio from microphone to WAV file."""

    def __init__(
        self,
        output_path: Path,
        sample_rate: int = 44100,
        channels: int = 1,
    ) -> None:
        self._output_path = output_path
        self._sample_rate = sample_rate
        self._channels = channels

        self._stream: sd.InputStream | None = None
        self._sound_file: sf.SoundFile | None = None

        self._recording = False
        self._paused = False
        self._lock = threading.Lock()

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
            else:
                self._sound_file.write(indata)

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

        self._stream.start()

    def pause(self) -> None:
        """Sets pause flag — callback writes silence instead of mic data."""
        with self._lock:
            self._paused = True

    def resume(self) -> None:
        """Clears pause flag — callback resumes writing mic data."""
        with self._lock:
            self._paused = False

    def stop(self) -> Path:
        """Closes stream and file, returns path to WAV."""
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
    def is_recording(self) -> bool:
        """True while the recorder is active (not yet stopped)."""
        with self._lock:
            return self._recording

    @property
    def is_paused(self) -> bool:
        """True while the recorder is paused."""
        with self._lock:
            return self._paused
