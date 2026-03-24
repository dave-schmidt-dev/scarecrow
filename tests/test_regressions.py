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
    """Batch transcription passes 16kHz audio directly to Whisper."""
    from scarecrow.transcriber import Transcriber

    # Create 1 second of 16kHz audio
    audio_16k = np.zeros(16000, dtype=np.float32)

    # Mock the batch model
    mock_segment = MagicMock()
    mock_segment.text = "hello world"
    mock_model = MagicMock()
    mock_model.transcribe.return_value = ([mock_segment], None)
    t = Transcriber()
    t._ready = True
    t._model_manager = MagicMock()
    t._model_manager.get_batch_model.return_value = mock_model

    t.transcribe_batch(audio_16k, batch_elapsed=30)

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


# ---------------------------------------------------------------------------
# Bug: drain_buffer overlap caused duplicate words in batch transcripts
# The 2s overlap meant the same words appeared at end of one batch and
# start of the next. drain_buffer must clear completely.
# ---------------------------------------------------------------------------


def test_drain_buffer_clears_completely(tmp_path: Path) -> None:
    """drain_buffer must return all audio and leave buffer empty — no overlap."""
    from scarecrow.recorder import AudioRecorder

    with patch("scarecrow.recorder.sd"), patch("scarecrow.recorder.sf"):
        recorder = AudioRecorder(tmp_path / "audio.wav", sample_rate=16000)
        recorder.start()

        # Simulate 3 seconds of audio (48 chunks of 1024 = 49152 samples)
        for _ in range(48):
            indata = np.ones((1024, 1), dtype="int16") * 100
            recorder._callback(indata, 1024, None, None)

        first = recorder.drain_buffer()
        assert first is not None
        assert len(first) == 48 * 1024

        # Second drain must be empty — no overlap retained
        second = recorder.drain_buffer()
        assert second is None
        recorder.stop()


# ---------------------------------------------------------------------------
# Bug: live pane used multiple disjoint widgets causing empty space
# and text rendering outside the bordered area. Single pane is required.
# ---------------------------------------------------------------------------


def test_live_pane_is_single_scrollable_container() -> None:
    """Live pane must render as one scrollable pane with a single content widget."""
    from textual.widgets import Static

    from scarecrow.app import ScarecrowApp

    async def _check():
        async with ScarecrowApp().run_test() as pilot:
            app = pilot.app
            live_pane = app.query_one("#live-pane")
            live_content = app.query_one("#live-content", Static)
            assert live_pane is not None
            assert live_content is not None

    import asyncio

    asyncio.get_event_loop().run_until_complete(_check())


# ---------------------------------------------------------------------------
# Bug: shutdown metrics not visible — TUI exits too fast.
# Metrics must be saved to app._shutdown_summary for __main__.py to print.
# ---------------------------------------------------------------------------


def test_shutdown_summary_saved_on_quit() -> None:
    """action_quit must save _shutdown_summary before exiting."""
    from scarecrow.app import ScarecrowApp

    async def _check():
        async with ScarecrowApp().run_test() as pilot:
            app = pilot.app
            with patch.object(app, "_deferred_quit"):
                app.action_quit()
                await pilot.pause()
            assert hasattr(app, "_shutdown_summary")
            assert "Duration" in app._shutdown_summary
            assert "Words" in app._shutdown_summary

    import asyncio

    asyncio.get_event_loop().run_until_complete(_check())


# ---------------------------------------------------------------------------
# Bug: HF Hub warning on startup when model not cached
# ---------------------------------------------------------------------------


def test_hf_warning_suppressed() -> None:
    """The HF_HUB_DISABLE_IMPLICIT_TOKEN env var must be set at import time."""
    import importlib
    import os

    # Import __main__ which sets the env var at module level
    importlib.import_module("scarecrow.__main__")
    assert os.environ.get("HF_HUB_DISABLE_IMPLICIT_TOKEN") == "1"


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
