#!/usr/bin/env python3
"""Run pytest in a subprocess-friendly way.

conftest.py registers an atexit handler that terminates PortAudio before
interpreter teardown, which is the real fix for CoreAudio segfaults.
os._exit() is kept as a safety net to skip any remaining teardown hazards.
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
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(exit_code)
