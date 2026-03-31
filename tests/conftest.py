"""Shared pytest configuration — PortAudio teardown fix + test helpers.

The root cause of test-suite segfaults is PortAudio's CoreAudio IOThread
firing callbacks into a half-torn-down Python interpreter.  We register an
atexit handler that terminates PortAudio *before* Python begins module
teardown, eliminating the race.
"""

from __future__ import annotations

import atexit
from unittest.mock import MagicMock, patch

import numpy as np
import pytest


def _terminate_portaudio() -> None:
    """Shut down PortAudio cleanly before interpreter teardown."""
    try:
        import sounddevice as _sd

        _sd._terminate()
    except Exception:
        pass


atexit.register(_terminate_portaudio)


# ---------------------------------------------------------------------------
# Shared test helpers — importable by any test module
# ---------------------------------------------------------------------------


def _mock_sys_capture() -> MagicMock:
    """Return a mock SystemAudioCapture that doesn't touch hardware."""
    mock = MagicMock()
    mock.is_recording = True
    mock.is_paused = False
    mock.peak_level = 0.0
    mock.buffer_seconds = 0.0
    mock.start.return_value = None
    mock.stop.return_value = None
    mock.pause.return_value = None
    mock.resume.return_value = None
    mock.drain_to_silence.return_value = None
    mock.drain_buffer.return_value = None
    return mock


def make_speech_chunk(n_samples: int = 1024, amplitude: int = 8000) -> np.ndarray:
    """Synthetic int16 speech chunk for ``recorder._callback()``."""
    return np.full((n_samples, 1), amplitude, dtype="int16")


def make_silence_chunk(n_samples: int = 1024) -> np.ndarray:
    """Synthetic int16 silence chunk for ``recorder._callback()``."""
    return np.zeros((n_samples, 1), dtype="int16")


def make_sys_speech_chunk(
    n_samples: int = 1024, channels: int = 2, amplitude: int = 8000
) -> np.ndarray:
    """Stereo int16 speech chunk for ``sys_capture._callback_inner()``."""
    return np.full((n_samples, channels), amplitude, dtype="int16")


# ---------------------------------------------------------------------------
# Autouse fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_summarizer():
    """Prevent the real summarization engine from running during tests.

    Patches at the source module so the cleanup flow in app.py is still
    exercised — only the heavy GGUF model loading is skipped.  Summarizer
    unit tests are unaffected because they import the function at module
    level before this fixture activates.
    """
    with patch("scarecrow.summarizer.summarize_session", return_value=None):
        yield
