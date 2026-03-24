"""Regression tests for bugs found during testing sessions."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

# ---------------------------------------------------------------------------
# Bug: model cache path detection wrong (dots replaced with dashes)
# ---------------------------------------------------------------------------


def test_model_cache_path_preserves_dots(tmp_path: Path) -> None:
    """_model_cache_path must not mangle dots in model names like 'tiny.en'."""
    from scarecrow.__main__ import _model_cache_path

    # Create a fake cache directory matching HuggingFace's naming
    cache_dir = tmp_path / ".cache" / "huggingface" / "hub"
    model_dir = cache_dir / "models--Systran--faster-whisper-tiny.en"
    model_dir.mkdir(parents=True)

    with patch("scarecrow.__main__.Path.home", return_value=tmp_path):
        result = _model_cache_path("tiny.en")
        assert result is not None
        assert "tiny.en" in str(result)


def test_model_cache_path_returns_none_when_not_cached(tmp_path: Path) -> None:
    """_model_cache_path returns None when the model isn't downloaded."""
    from scarecrow.__main__ import _model_cache_path

    with patch("scarecrow.__main__.Path.home", return_value=tmp_path):
        result = _model_cache_path("tiny.en")
        assert result is None


def test_model_cache_path_works_for_large_v3(tmp_path: Path) -> None:
    """large-v3 model name has no dots — should still work."""
    from scarecrow.__main__ import _model_cache_path

    cache_dir = tmp_path / ".cache" / "huggingface" / "hub"
    model_dir = cache_dir / "models--Systran--faster-whisper-large-v3"
    model_dir.mkdir(parents=True)

    with patch("scarecrow.__main__.Path.home", return_value=tmp_path):
        result = _model_cache_path("large-v3")
        assert result is not None


# ---------------------------------------------------------------------------
# Bug: batch transcription gets 44100Hz audio but Whisper expects 16000Hz
# ---------------------------------------------------------------------------


def test_drain_buffer_returns_float32(tmp_path: Path) -> None:
    """drain_buffer must return float32 audio normalized to [-1, 1]."""
    from scarecrow.recorder import AudioRecorder

    with patch("scarecrow.recorder.sd"), patch("scarecrow.recorder.sf"):
        recorder = AudioRecorder(tmp_path / "audio.wav", sample_rate=44100)
        recorder.start()

        # Simulate audio callback with int16 data
        indata = (np.random.randn(1024, 1) * 10000).astype("int16")
        recorder._callback(indata, 1024, None, None)

        audio = recorder.drain_buffer()
        assert audio is not None
        assert audio.dtype == np.float32
        assert audio.max() <= 1.0
        assert audio.min() >= -1.0
        recorder.stop()


def test_drain_buffer_sample_rate_is_44100(tmp_path: Path) -> None:
    """drain_buffer returns audio at recorder's sample rate (44100), not 16000.

    The batch transcription code must resample before passing to Whisper.
    """
    from scarecrow.recorder import AudioRecorder

    with patch("scarecrow.recorder.sd"), patch("scarecrow.recorder.sf"):
        recorder = AudioRecorder(tmp_path / "audio.wav", sample_rate=16000)
        recorder.start()

        # Simulate 1 second of audio at 16000Hz
        for _ in range(16):  # ~16 chunks of 1024 = ~16384 samples
            indata = np.zeros((1024, 1), dtype="int16")
            recorder._callback(indata, 1024, None, None)

        audio = recorder.drain_buffer()
        assert audio is not None
        # Should have ~16384 samples at 16kHz
        assert len(audio) > 15000
        assert recorder.sample_rate == 16000
        recorder.stop()


def test_batch_passes_audio_directly_at_16khz() -> None:
    """_run_batch passes 16kHz audio directly to Whisper (no resampling)."""
    from scarecrow.app import ScarecrowApp

    app = ScarecrowApp()

    # Create 1 second of 16kHz audio
    audio_16k = np.zeros(16000, dtype=np.float32)

    # Mock the batch model
    mock_segment = MagicMock()
    mock_segment.text = "hello world"
    mock_model = MagicMock()
    mock_model.transcribe.return_value = ([mock_segment], None)
    app._batch_model = mock_model

    # Run batch — audio should pass through unchanged
    with patch.object(app, "_safe_call"):
        app._run_batch(audio_16k, batch_elapsed=30)

    mock_model.transcribe.assert_called_once()
    transcribed_audio = mock_model.transcribe.call_args[0][0]
    assert len(transcribed_audio) == 16000


# ---------------------------------------------------------------------------
# Bug: audio buffer not populated (callback doesn't buffer when paused)
# ---------------------------------------------------------------------------


def test_callback_does_not_buffer_when_paused(tmp_path: Path) -> None:
    """Paused callback should NOT accumulate audio in the batch buffer."""
    from scarecrow.recorder import AudioRecorder

    with patch("scarecrow.recorder.sd"), patch("scarecrow.recorder.sf"):
        recorder = AudioRecorder(tmp_path / "audio.wav")
        recorder.start()
        recorder.pause()

        indata = np.zeros((1024, 1), dtype="int16")
        recorder._callback(indata, 1024, None, None)

        audio = recorder.drain_buffer()
        assert audio is None


def test_callback_buffers_when_recording(tmp_path: Path) -> None:
    """Recording callback must accumulate audio in the batch buffer."""
    from scarecrow.recorder import AudioRecorder

    with patch("scarecrow.recorder.sd"), patch("scarecrow.recorder.sf"):
        recorder = AudioRecorder(tmp_path / "audio.wav")
        recorder.start()

        indata = np.ones((1024, 1), dtype="int16") * 500
        recorder._callback(indata, 1024, None, None)

        audio = recorder.drain_buffer()
        assert audio is not None
        assert len(audio) == 1024


# ---------------------------------------------------------------------------
# Bug: drain_buffer empties on second call (double-drain returns nothing)
# ---------------------------------------------------------------------------


def test_drain_buffer_empties_after_drain(tmp_path: Path) -> None:
    """drain_buffer should return None on second call with no new audio."""
    from scarecrow.recorder import AudioRecorder

    with patch("scarecrow.recorder.sd"), patch("scarecrow.recorder.sf"):
        recorder = AudioRecorder(tmp_path / "audio.wav")
        recorder.start()

        indata = np.zeros((1024, 1), dtype="int16")
        recorder._callback(indata, 1024, None, None)

        first = recorder.drain_buffer()
        assert first is not None

        second = recorder.drain_buffer()
        assert second is None
        recorder.stop()


# ---------------------------------------------------------------------------
# Audio level meter: peak_level tracks mic input
# ---------------------------------------------------------------------------


def test_peak_level_updates_on_audio(tmp_path: Path) -> None:
    """peak_level should reflect the loudest sample in the last callback."""
    from scarecrow.recorder import AudioRecorder

    with patch("scarecrow.recorder.sd"), patch("scarecrow.recorder.sf"):
        recorder = AudioRecorder(tmp_path / "audio.wav")
        recorder.start()

        assert recorder.peak_level == 0.0

        # Simulate loud audio (half of int16 max)
        indata = np.ones((1024, 1), dtype="int16") * 16384
        recorder._callback(indata, 1024, None, None)

        assert recorder.peak_level == pytest.approx(0.5, abs=0.01)
        recorder.stop()


def test_peak_level_zero_when_paused(tmp_path: Path) -> None:
    """peak_level should be 0 when paused."""
    from scarecrow.recorder import AudioRecorder

    with patch("scarecrow.recorder.sd"), patch("scarecrow.recorder.sf"):
        recorder = AudioRecorder(tmp_path / "audio.wav")
        recorder.start()
        recorder.pause()

        indata = np.ones((1024, 1), dtype="int16") * 16384
        recorder._callback(indata, 1024, None, None)

        assert recorder.peak_level == 0.0
        recorder.stop()


# ---------------------------------------------------------------------------
# Bug: editable install breaks when run from outside project directory
# (Homebrew Python skips _scarecrow.pth, import fails)
# ---------------------------------------------------------------------------


def test_scarecrow_importable_from_outside_project_dir() -> None:
    """scarecrow must be importable without the project dir on sys.path."""
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-c", "from scarecrow.__main__ import main"],
        capture_output=True,
        text=True,
        cwd="/tmp",
    )
    assert result.returncode == 0, f"Import failed from /tmp: {result.stderr}"
