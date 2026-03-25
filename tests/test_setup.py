"""Tests for the interactive setup helper."""

from __future__ import annotations

from pathlib import Path

from scripts import setup as setup_script


def test_write_config_updates_current_models_without_exact_default_match(
    tmp_path: Path,
) -> None:
    """write_config should replace whatever model names are currently in config.py."""
    project_root = tmp_path / "project"
    config_path = project_root / "scarecrow" / "config.py"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        'REALTIME_MODEL = "tiny.en"\nFINAL_MODEL = "small.en"\n',
        encoding="utf-8",
    )

    original_file = setup_script.__file__
    try:
        setup_script.__file__ = str(project_root / "scripts" / "setup.py")
        setup_script.write_config("base.en", "medium.en")
    finally:
        setup_script.__file__ = original_file

    updated = config_path.read_text(encoding="utf-8")
    assert 'REALTIME_MODEL = "base.en"' in updated
    assert 'FINAL_MODEL = "medium.en"' in updated


def test_write_config_treats_model_names_as_plain_text(tmp_path: Path) -> None:
    """Replacement text must not interpret regex backreferences."""
    project_root = tmp_path / "project"
    config_path = project_root / "scarecrow" / "config.py"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        'REALTIME_MODEL = "base.en"\nFINAL_MODEL = "medium.en"\n',
        encoding="utf-8",
    )

    original_file = setup_script.__file__
    try:
        setup_script.__file__ = str(project_root / "scripts" / "setup.py")
        setup_script.write_config(r"x\1y", r"x\g<2>z")
    finally:
        setup_script.__file__ = original_file

    updated = config_path.read_text(encoding="utf-8")
    assert 'REALTIME_MODEL = "x\\1y"' in updated
    assert 'FINAL_MODEL = "x\\g<2>z"' in updated
