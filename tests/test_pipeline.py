"""Tests for the callback-driven audio pipeline.

Uses real AudioRecorder and SystemAudioCapture objects with hardware mocked.
Audio is injected via the real _callback() / _callback_inner() methods so
the full VAD, downmix, and drain logic runs without touching any device.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SAMPLE_RATE_CAPTURE = 48000
SAMPLE_RATE_STT = 16000
CHUNK_SAMPLES = 4800  # 100 ms at 48kHz


def _speech_chunk(n_samples: int = CHUNK_SAMPLES, channels: int = 1) -> np.ndarray:
    """int16 array with amplitude well above the default silence threshold."""
    shape = (n_samples, channels) if channels > 1 else (n_samples, 1)
    return np.full(shape, 8000, dtype="int16")


def _silence_chunk(n_samples: int = CHUNK_SAMPLES, channels: int = 1) -> np.ndarray:
    """int16 array of zeros (RMS == 0.0, always below silence threshold)."""
    shape = (n_samples, channels) if channels > 1 else (n_samples, 1)
    return np.zeros(shape, dtype="int16")


def _make_mock_sd(channels: int = 1) -> tuple[MagicMock, MagicMock]:
    """Return (mock_sd_module, mock_stream) with device query configured."""
    mock_sd = MagicMock()
    mock_stream = MagicMock()
    mock_sd.InputStream.return_value = mock_stream
    mock_sd.query_devices.return_value = {
        "default_samplerate": float(SAMPLE_RATE_CAPTURE),
        "max_input_channels": channels,
    }
    mock_sd.default.device = [0, 0]
    return mock_sd, mock_stream


def _make_mock_sf() -> tuple[MagicMock, MagicMock]:
    """Return (mock_sf_module, mock_file)."""
    mock_sf = MagicMock()
    mock_file = MagicMock()
    mock_sf.SoundFile.return_value = mock_file
    return mock_sf, mock_file


# ---------------------------------------------------------------------------
# Mic pipeline: callback → VAD → drain
# ---------------------------------------------------------------------------


def test_mic_callback_to_drain_produces_audio(tmp_path: Path) -> None:
    """10 speech + 8 silence chunks → drain_to_silence returns non-None audio.

    VAD_MIN_SILENCE_MS=750ms at 100ms/chunk requires 8 consecutive silent
    chunks.  We feed exactly 8 to hit the silence boundary.
    """
    from scarecrow.config import Config
    from scarecrow.recorder import AudioRecorder

    mock_sd, _ = _make_mock_sd(channels=1)
    mock_sf, _ = _make_mock_sf()

    with patch.dict("sys.modules", {"sounddevice": mock_sd, "soundfile": mock_sf}):
        cfg = Config()
        recorder = AudioRecorder(output_path=tmp_path / "mic.wav", cfg=cfg)
        recorder.start()

        try:
            for _ in range(10):
                recorder._callback(_speech_chunk(), CHUNK_SAMPLES, None, None)
            # 8 x 100ms = 800ms ≥ VAD_MIN_SILENCE_MS (750ms) → triggers drain
            for _ in range(8):
                recorder._callback(_silence_chunk(), CHUNK_SAMPLES, None, None)

            result = recorder.drain_to_silence(
                silence_threshold=cfg.VAD_SILENCE_THRESHOLD,
                min_silence_ms=cfg.VAD_MIN_SILENCE_MS,
                max_buffer_seconds=cfg.VAD_MAX_BUFFER_SECONDS,
            )
        finally:
            recorder.stop()

    assert result is not None
    audio, energies = result
    assert audio.dtype == np.float32
    assert len(audio) > 0
    assert len(energies) > 0


def test_mic_insufficient_silence_does_not_drain(tmp_path: Path) -> None:
    """Speech with fewer silence chunks than min_silent_chunks → drain returns None.

    VAD_MIN_SILENCE_MS=750ms at 100ms/chunk needs 8 silent chunks.
    Feed only 5 silence chunks so the boundary is never found, and total
    audio is well under VAD_MAX_BUFFER_SECONDS so no hard drain fires either.
    """
    from scarecrow.config import Config
    from scarecrow.recorder import AudioRecorder

    mock_sd, _ = _make_mock_sd(channels=1)
    mock_sf, _ = _make_mock_sf()

    with patch.dict("sys.modules", {"sounddevice": mock_sd, "soundfile": mock_sf}):
        cfg = Config()
        recorder = AudioRecorder(output_path=tmp_path / "mic.wav", cfg=cfg)
        recorder.start()

        try:
            for _ in range(10):
                recorder._callback(_speech_chunk(), CHUNK_SAMPLES, None, None)
            # Only 5 x 100ms = 500ms of silence — below the 750ms threshold
            for _ in range(5):
                recorder._callback(_silence_chunk(), CHUNK_SAMPLES, None, None)

            result = recorder.drain_to_silence(
                silence_threshold=cfg.VAD_SILENCE_THRESHOLD,
                min_silence_ms=cfg.VAD_MIN_SILENCE_MS,
                max_buffer_seconds=cfg.VAD_MAX_BUFFER_SECONDS,
            )
        finally:
            recorder.stop()

    # Not enough silence and not at max buffer → VAD keeps accumulating
    assert result is None


def test_mic_hard_drain_after_max_buffer(tmp_path: Path) -> None:
    """Continuous speech exceeding VAD_MAX_BUFFER_SECONDS triggers a hard drain."""
    from scarecrow.config import Config
    from scarecrow.recorder import AudioRecorder

    mock_sd, _ = _make_mock_sd(channels=1)
    mock_sf, _ = _make_mock_sf()

    # Use a very short max buffer so the test doesn't need 30 real seconds
    cfg = Config(VAD_MAX_BUFFER_SECONDS=1)

    with patch.dict("sys.modules", {"sounddevice": mock_sd, "soundfile": mock_sf}):
        recorder = AudioRecorder(output_path=tmp_path / "mic.wav", cfg=cfg)
        recorder.start()

        try:
            # 1 second at 48kHz with 4800-sample chunks = 10 chunks = exactly
            # VAD_MAX_BUFFER_SECONDS; push a couple extra to be safe
            for _ in range(12):
                recorder._callback(_speech_chunk(), CHUNK_SAMPLES, None, None)

            result = recorder.drain_to_silence(
                silence_threshold=cfg.VAD_SILENCE_THRESHOLD,
                min_silence_ms=cfg.VAD_MIN_SILENCE_MS,
                max_buffer_seconds=cfg.VAD_MAX_BUFFER_SECONDS,
            )
        finally:
            recorder.stop()

    assert result is not None
    audio, energies = result
    assert len(audio) > 0
    # Buffer should be empty after hard drain
    assert len(energies) > 0


def test_mic_paused_callback_no_buffer(tmp_path: Path) -> None:
    """Chunks fed while paused are not added to the transcription buffer."""
    from scarecrow.config import Config
    from scarecrow.recorder import AudioRecorder

    mock_sd, _ = _make_mock_sd(channels=1)
    mock_sf, _ = _make_mock_sf()

    with patch.dict("sys.modules", {"sounddevice": mock_sd, "soundfile": mock_sf}):
        cfg = Config()
        recorder = AudioRecorder(output_path=tmp_path / "mic.wav", cfg=cfg)
        recorder.start()

        try:
            recorder.pause()

            for _ in range(10):
                recorder._callback(_speech_chunk(), CHUNK_SAMPLES, None, None)

            result = recorder.drain_buffer()
        finally:
            recorder.stop()

    assert result is None


# ---------------------------------------------------------------------------
# Sys audio pipeline: callback → downmix → VAD → drain
# ---------------------------------------------------------------------------


def test_sys_stereo_downmix_to_mono(tmp_path: Path) -> None:
    """Stereo chunks fed through _callback_inner produce mono float32 on drain."""
    mock_sd, _ = _make_mock_sd(channels=2)
    mock_sf, _ = _make_mock_sf()

    with patch.dict("sys.modules", {"sounddevice": mock_sd, "soundfile": mock_sf}):
        from scarecrow.sys_audio import SystemAudioCapture

        capture = SystemAudioCapture(output_path=tmp_path / "sys.wav", device=0)
        capture.start()

        try:
            stereo = _speech_chunk(n_samples=CHUNK_SAMPLES, channels=2)
            for _ in range(5):
                capture._callback_inner(stereo, status=None)

            audio = capture.drain_buffer()
        finally:
            capture.stop()

    assert audio is not None
    assert audio.dtype == np.float32
    assert audio.ndim == 1  # mono


def test_sys_drain_to_silence_with_sys_thresholds(tmp_path: Path) -> None:
    """Speech + silence with sys-specific thresholds → drain_to_silence fires."""
    mock_sd, _ = _make_mock_sd(channels=2)
    mock_sf, _ = _make_mock_sf()

    with patch.dict("sys.modules", {"sounddevice": mock_sd, "soundfile": mock_sf}):
        from scarecrow.sys_audio import SystemAudioCapture

        capture = SystemAudioCapture(output_path=tmp_path / "sys.wav", device=0)
        capture.start()

        try:
            # 10 speech chunks (high RMS)
            for _ in range(10):
                capture._callback_inner(_speech_chunk(channels=2), status=None)
            # 8 silence chunks — silence RMS is 0.0, well below SYS threshold 0.003
            # At 100ms/chunk, 8 chunks = 800ms > SYS_VAD_MIN_SILENCE_MS (750ms)
            for _ in range(8):
                capture._callback_inner(_silence_chunk(channels=2), status=None)

            result = capture.drain_to_silence(
                silence_threshold=0.003,
                min_silence_ms=750,
                max_buffer_seconds=30,
            )
        finally:
            capture.stop()

    assert result is not None
    audio, _energies = result
    assert audio.dtype == np.float32
    assert len(audio) > 0


def test_sys_drain_buffer_returns_16khz(tmp_path: Path) -> None:
    """drain_buffer resamples 48kHz capture to 16kHz (length ~1/3 of input)."""
    mock_sd, _ = _make_mock_sd(channels=2)
    mock_sf, _ = _make_mock_sf()

    with patch.dict("sys.modules", {"sounddevice": mock_sd, "soundfile": mock_sf}):
        from scarecrow.sys_audio import SystemAudioCapture

        capture = SystemAudioCapture(output_path=tmp_path / "sys.wav", device=0)
        capture.start()

        try:
            # Feed exactly 1 second of stereo audio: 10 x 4800-sample chunks
            for _ in range(10):
                capture._callback_inner(_speech_chunk(channels=2), status=None)

            audio = capture.drain_buffer()
        finally:
            capture.stop()

    assert audio is not None
    # 1 second at 48kHz = 48000 input samples (mono after downmix)
    # Resampled to 16kHz → 16000 samples (allow ±16 for linear-interp rounding)
    assert abs(len(audio) - 16000) <= 16


def test_sys_buffer_seconds_tracking(tmp_path: Path) -> None:
    """buffer_seconds > 0 after feeding chunks; 0 after drain."""
    mock_sd, _ = _make_mock_sd(channels=2)
    mock_sf, _ = _make_mock_sf()

    with patch.dict("sys.modules", {"sounddevice": mock_sd, "soundfile": mock_sf}):
        from scarecrow.sys_audio import SystemAudioCapture

        capture = SystemAudioCapture(output_path=tmp_path / "sys.wav", device=0)
        capture.start()

        try:
            for _ in range(5):
                capture._callback_inner(_speech_chunk(channels=2), status=None)

            assert capture.buffer_seconds > 0.0

            capture.drain_buffer()

            assert capture.buffer_seconds == 0.0
        finally:
            capture.stop()


# ---------------------------------------------------------------------------
# Transcriber integration: mock model, real routing
# ---------------------------------------------------------------------------


def _make_transcriber():
    """Return a Transcriber with ModelManager mocked out (no real model)."""
    from unittest.mock import MagicMock

    from scarecrow.runtime import ModelManager
    from scarecrow.transcriber import Transcriber

    mm = MagicMock(spec=ModelManager)
    mm.prepare.return_value = None
    transcriber = Transcriber(model_manager=mm)
    return transcriber


def test_transcriber_mic_source_fires_batch_callback() -> None:
    """transcribe_batch(source='mic') invokes on_batch_result."""
    from unittest.mock import MagicMock, patch

    from scarecrow.transcriber import TranscriberBindings

    transcriber = _make_transcriber()
    transcriber.prepare()

    on_batch = MagicMock()
    transcriber.bind(TranscriberBindings(on_batch_result=on_batch))

    audio = np.zeros(16000, dtype=np.float32)

    with patch.object(transcriber, "_transcribe_parakeet", return_value="hello world"):
        result = transcriber.transcribe_batch(audio, batch_elapsed=5, source="mic")

    assert result == "hello world"
    on_batch.assert_called_once_with("hello world", 5)


def test_transcriber_sys_source_fires_sys_callback() -> None:
    """transcribe_batch(source='sys') invokes on_sys_batch_result."""
    from unittest.mock import MagicMock, patch

    from scarecrow.transcriber import TranscriberBindings

    transcriber = _make_transcriber()
    transcriber.prepare()

    on_batch = MagicMock()
    on_sys = MagicMock()
    transcriber.bind(
        TranscriberBindings(on_batch_result=on_batch, on_sys_batch_result=on_sys)
    )

    audio = np.zeros(16000, dtype=np.float32)

    with patch.object(transcriber, "_transcribe_parakeet", return_value="system audio"):
        result = transcriber.transcribe_batch(audio, batch_elapsed=3, source="sys")

    assert result == "system audio"
    on_sys.assert_called_once_with("system audio", 3)
    on_batch.assert_not_called()


def test_transcriber_sys_fallback_to_batch_callback() -> None:
    """source='sys' falls back to on_batch_result without sys cb."""
    from unittest.mock import MagicMock, patch

    from scarecrow.transcriber import TranscriberBindings

    transcriber = _make_transcriber()
    transcriber.prepare()

    on_batch = MagicMock()
    # Bind only the mic callback — no on_sys_batch_result
    transcriber.bind(TranscriberBindings(on_batch_result=on_batch))

    audio = np.zeros(16000, dtype=np.float32)

    with patch.object(
        transcriber, "_transcribe_parakeet", return_value="fallback text"
    ):
        result = transcriber.transcribe_batch(audio, batch_elapsed=7, source="sys")

    assert result == "fallback text"
    on_batch.assert_called_once_with("fallback text", 7)
