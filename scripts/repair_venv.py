#!/usr/bin/env python3
"""Repair and validate the local editable-install virtualenv state."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scarecrow.env_health import (  # noqa: E402
    ensure_editable_install_visible,
    verify_import_outside_project,
)


def main() -> int:
    project_root = PROJECT_ROOT

    repaired_path = ensure_editable_install_visible(
        "scarecrow",
        project_root=project_root,
        venv_root=project_root / ".venv",
    )
    verify_import_outside_project(
        "scarecrow",
        project_root=project_root,
        venv_root=project_root / ".venv",
    )

    print(f"ok: {repaired_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
