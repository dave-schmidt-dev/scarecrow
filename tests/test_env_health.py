"""Tests for editable-install environment health helpers."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from scarecrow.env_health import (
    UF_HIDDEN,
    clear_hidden_flag,
    editable_pth_path,
    ensure_editable_install_visible,
    has_hidden_flag,
)


@pytest.mark.skipif(not hasattr(os, "chflags"), reason="requires macOS chflags")
def test_clear_hidden_flag_removes_hidden_bit(tmp_path: Path) -> None:
    """The repair helper must clear UF_HIDDEN from an editable .pth file."""
    pth_path = tmp_path / "_demo.pth"
    pth_path.write_text("/tmp/demo\n", encoding="utf-8")
    os.chflags(pth_path, os.stat(pth_path).st_flags | UF_HIDDEN)

    assert has_hidden_flag(pth_path) is True
    changed = clear_hidden_flag(pth_path)

    assert changed is True
    assert has_hidden_flag(pth_path) is False


def test_editable_pth_path_uses_active_python_minor() -> None:
    """The helper should point at the active venv site-packages layout."""
    path = editable_pth_path("scarecrow", venv_root=Path("/tmp/demo-venv"))
    assert str(path).endswith("/site-packages/_scarecrow.pth")


@pytest.mark.skipif(not hasattr(os, "chflags"), reason="requires macOS chflags")
def test_ensure_editable_install_visible_repairs_hidden_pth(tmp_path: Path) -> None:
    """A hidden editable .pth should be repaired and validated."""
    project_root = tmp_path / "project"
    venv_root = tmp_path / "venv"
    project_root.mkdir()
    site_packages = (
        venv_root
        / "lib"
        / f"python{os.sys.version_info.major}.{os.sys.version_info.minor}"
        / "site-packages"
    )
    site_packages.mkdir(parents=True)
    pth_path = site_packages / "_demo.pth"
    pth_path.write_text(f"{project_root}\n", encoding="utf-8")
    os.chflags(pth_path, os.stat(pth_path).st_flags | UF_HIDDEN)

    repaired = ensure_editable_install_visible(
        "demo",
        project_root=project_root,
        venv_root=venv_root,
    )

    assert repaired == pth_path
    assert has_hidden_flag(pth_path) is False
