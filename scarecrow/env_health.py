"""Helpers for keeping the local editable environment healthy."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

UF_HIDDEN = getattr(__import__("stat"), "UF_HIDDEN", 0x8000)


def editable_pth_path(project_name: str, venv_root: Path | None = None) -> Path:
    """Return the editable-install .pth file for a project."""
    root = Path(".venv") if venv_root is None else venv_root
    matches = sorted(root.glob(f"lib/python*/site-packages/_{project_name}.pth"))
    if matches:
        return matches[0]
    python_dir = f"python{sys.version_info.major}.{sys.version_info.minor}"
    return root / "lib" / python_dir / "site-packages" / f"_{project_name}.pth"


def has_hidden_flag(path: Path) -> bool:
    """Return True when the macOS hidden flag is set for a path."""
    return bool(os.stat(path).st_flags & UF_HIDDEN)


def clear_hidden_flag(path: Path) -> bool:
    """Clear the macOS hidden flag if present. Returns True if it changed."""
    flags = os.stat(path).st_flags
    if not flags & UF_HIDDEN:
        return False
    os.chflags(path, flags & ~UF_HIDDEN)
    return True


def ensure_editable_install_visible(
    project_name: str,
    *,
    project_root: Path | None = None,
    venv_root: Path | None = None,
) -> Path:
    """Ensure the editable-install .pth file exists and is not hidden."""
    root = Path.cwd() if project_root is None else project_root
    pth_path = editable_pth_path(project_name, venv_root=venv_root)
    if not pth_path.exists():
        msg = f"Editable install path file not found: {pth_path}"
        raise FileNotFoundError(msg)
    clear_hidden_flag(pth_path)
    expected = root.resolve()
    actual = Path(pth_path.read_text(encoding="utf-8").strip()).resolve()
    if actual != expected:
        msg = f"Editable install points to {actual}, expected {expected}"
        raise RuntimeError(msg)
    return pth_path


def verify_import_outside_project(
    project_name: str,
    *,
    project_root: Path | None = None,
    venv_root: Path | None = None,
) -> None:
    """Verify the package can be imported when cwd is outside the project root."""
    root = Path.cwd() if project_root is None else project_root
    venv = Path(".venv") if venv_root is None else venv_root
    python_path = venv / "bin" / "python"
    if not python_path.exists():
        msg = f"Virtualenv interpreter not found: {python_path}"
        raise FileNotFoundError(msg)

    result = subprocess.run(
        [str(python_path), "-c", f"import {project_name}"],
        capture_output=True,
        text=True,
        cwd="/tmp",
        check=False,
    )
    if result.returncode != 0:
        msg = (
            f"Import check failed outside project root for {project_name}.\n"
            f"Project root: {root}\n"
            f"stderr:\n{result.stderr}"
        )
        raise RuntimeError(msg)
