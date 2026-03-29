"""Shared pytest configuration — PortAudio teardown fix.

The root cause of test-suite segfaults is PortAudio's CoreAudio IOThread
firing callbacks into a half-torn-down Python interpreter.  We register an
atexit handler that terminates PortAudio *before* Python begins module
teardown, eliminating the race.
"""

from __future__ import annotations

import atexit


def _terminate_portaudio() -> None:
    """Shut down PortAudio cleanly before interpreter teardown."""
    try:
        import sounddevice as _sd

        _sd._terminate()
    except Exception:
        pass


atexit.register(_terminate_portaudio)
