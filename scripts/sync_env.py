#!/usr/bin/env python3
"""Run `uv sync` and immediately repair/validate the editable install."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def main() -> int:
    project_root = Path(__file__).resolve().parent.parent

    sync = subprocess.run(
        ["uv", "sync", *sys.argv[1:]],
        cwd=project_root,
        check=False,
    )
    if sync.returncode != 0:
        return sync.returncode

    repair = subprocess.run(
        [sys.executable, str(project_root / "scripts" / "repair_venv.py")],
        cwd=project_root,
        check=False,
    )
    return repair.returncode


if __name__ == "__main__":
    raise SystemExit(main())
