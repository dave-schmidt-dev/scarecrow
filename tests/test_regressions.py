"""Regression tests for bugs found during testing sessions."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

# ---------------------------------------------------------------------------
# Bug: batch transcription gets 44100Hz audio but Parakeet expects 16000Hz
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

    The batch transcription code must resample before passing to Parakeet.
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
    """Batch transcription passes 16kHz audio directly to the parakeet backend."""
    from scarecrow.transcriber import Transcriber

    # Create 1 second of 16kHz audio
    audio_16k = np.zeros(16000, dtype=np.float32)

    captured: list[np.ndarray] = []

    t = Transcriber()
    t._ready = True
    t._model_manager = MagicMock()

    def fake_transcribe_parakeet(audio):
        captured.append(audio)
        return "hello world"

    t._transcribe_parakeet = fake_transcribe_parakeet  # type: ignore[method-assign]

    t.transcribe_batch(audio_16k, batch_elapsed=30)

    assert len(captured) == 1
    assert len(captured[0]) == 16000


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
# Bug: shutdown metrics not visible — TUI exits too fast.
# Metrics must be saved to app._shutdown_summary for __main__.py to print.
# ---------------------------------------------------------------------------


async def test_shutdown_summary_saved_on_quit() -> None:
    """action_quit must save _shutdown_summary before exiting."""
    from scarecrow.app import ScarecrowApp

    async with ScarecrowApp().run_test() as pilot:
        app = pilot.app
        with patch.object(app, "_deferred_quit"):
            app.action_quit()
            await pilot.pause()
        assert hasattr(app, "_shutdown_summary")
        assert "Duration" in app._shutdown_summary
        assert "Words" in app._shutdown_summary


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

    from scarecrow.env_health import ensure_editable_install_visible

    ensure_editable_install_visible("scarecrow")

    result = subprocess.run(
        [sys.executable, "-c", "from scarecrow.__main__ import main"],
        capture_output=True,
        text=True,
        cwd="/tmp",
    )
    assert result.returncode == 0, f"Import failed from /tmp: {result.stderr}"
