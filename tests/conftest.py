"""Shared pytest configuration — PortAudio teardown fix.

The root cause of test-suite segfaults is PortAudio's CoreAudio IOThread
firing callbacks into a half-torn-down Python interpreter.  We register an
atexit handler that terminates PortAudio *before* Python begins module
teardown, eliminating the race.
"""

from __future__ import annotations

import atexit
from unittest.mock import patch

import pytest


def _terminate_portaudio() -> None:
    """Shut down PortAudio cleanly before interpreter teardown."""
    try:
        import sounddevice as _sd

        _sd._terminate()
    except Exception:
        pass


atexit.register(_terminate_portaudio)


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
