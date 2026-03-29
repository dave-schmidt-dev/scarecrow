#!/usr/bin/env python3
"""Interactive setup for Scarecrow — installs deps, validates env, shows config.

Keep this script in sync with README.md and pyproject.toml.
If you change requirements, launch methods, or architecture, update this too.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def print_header():
    print()
    print("=" * 60)
    print("  Scarecrow Setup")
    print("=" * 60)
    print()


def check_python_version():
    """Verify Python 3.12+."""
    print("CHECKING PYTHON VERSION")
    print("-" * 40)
    v = sys.version_info
    print(f"  Python {v.major}.{v.minor}.{v.micro}")
    if (v.major, v.minor) < (3, 12):
        print("  ✗ Python 3.12+ required")
        return False
    print("  ✓ OK")
    print()
    return True


def check_uv():
    """Check that uv is installed."""
    print("CHECKING UV")
    print("-" * 40)
    if shutil.which("uv"):
        print("  ✓ uv found")
    else:
        print("  ✗ uv not found — install from https://docs.astral.sh/uv/")
        print()
        return False
    print()
    return True


def install_deps():
    """Run sync_env.py to install deps and repair the editable install."""
    print("INSTALLING DEPENDENCIES")
    print("-" * 40)
    sync_script = PROJECT_ROOT / "scripts" / "sync_env.py"
    result = subprocess.run(
        [sys.executable, str(sync_script)],
        cwd=PROJECT_ROOT,
        check=False,
    )
    if result.returncode != 0:
        print("  ✗ Dependency install failed (see output above)")
        print()
        return False
    print("  ✓ Dependencies installed and editable install validated")
    print()
    return True


def install_hooks():
    """Install pre-commit hooks."""
    print("INSTALLING GIT HOOKS")
    print("-" * 40)
    venv_precommit = PROJECT_ROOT / ".venv" / "bin" / "pre-commit"
    if not venv_precommit.exists():
        print("  ⚠ pre-commit not found in .venv — skipping hooks")
        print()
        return True
    result = subprocess.run(
        [
            str(venv_precommit),
            "install",
            "--hook-type",
            "pre-commit",
            "--hook-type",
            "pre-push",
        ],
        cwd=PROJECT_ROOT,
        check=False,
    )
    if result.returncode != 0:
        print("  ⚠ Hook install failed (non-fatal)")
    else:
        print("  ✓ Pre-commit and pre-push hooks installed")
    print()
    return True


def explain_architecture():
    """Show how Scarecrow works."""
    print("HOW SCARECROW WORKS")
    print("-" * 40)
    print()
    print("Scarecrow uses parakeet-mlx for VAD-based batch transcription.")
    print("Audio drains at natural speech pauses (600ms+ silence),")
    print("with a 30-second hard max for continuous speech.")
    print()
    print("Backend: parakeet-mlx (Apple Silicon GPU)")
    print("Model:   mlx-community/parakeet-tdt-0.6b-v3")
    print("Requires: macOS with Apple Silicon + microphone access")
    print()


def setup_alias():
    """Show alias setup instructions."""
    print("LAUNCH SETUP")
    print("-" * 40)
    print()
    print("Option 1 — Shell alias (add to ~/.zshrc or ~/.bashrc):")
    print()
    print(f'  alias sc="{PROJECT_ROOT}/.venv/bin/scarecrow"')
    print()
    print("Option 2 — iTerm2 profile (see README.md for details):")
    print()
    print("  cp examples/scarecrow-iterm-profile.json \\")
    print("     ~/Library/Application\\ Support/iTerm2/DynamicProfiles/scarecrow.json")
    print()
    print("Avoid 'uv run' in aliases — it can re-trigger the macOS UF_HIDDEN")
    print("flag on the editable-install .pth file.")
    print()


def main():
    print_header()

    if not check_python_version():
        return 1
    if not check_uv():
        return 1
    if not install_deps():
        return 1
    install_hooks()
    explain_architecture()
    setup_alias()

    print("You're all set! Run `sc` to start Scarecrow.")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
