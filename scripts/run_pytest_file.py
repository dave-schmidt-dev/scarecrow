#!/usr/bin/env python3
"""Run pytest and exit without interpreter teardown side effects.

This repository can trigger native-extension crashes after pytest has already
finished and reported a final status. Running pytest through `os._exit()`
preserves the real test result while bypassing the unstable interpreter
shutdown path.
"""

from __future__ import annotations

import os
import sys

import pytest


def main(argv: list[str]) -> int:
    """Run pytest with the provided arguments."""
    return int(pytest.main(argv))


if __name__ == "__main__":
    exit_code = main(sys.argv[1:])
    # Terminate PortAudio before os._exit() so CoreAudio's IOThread doesn't
    # segfault trying to call back into a half-torn-down Python interpreter.
    try:
        import sounddevice as _sd

        _sd._terminate()
    except Exception:
        pass
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(exit_code)
