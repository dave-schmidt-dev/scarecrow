"""Tests for the interactive setup helper."""

from __future__ import annotations

from scripts import setup as setup_script


def test_setup_alias_prints_project_dir(capsys) -> None:
    """setup_alias should print a shell alias pointing to the project directory."""
    setup_script.setup_alias()
    output = capsys.readouterr().out
    assert "alias sc=" in output
    assert ".venv/bin/scarecrow" in output


def test_explain_architecture_mentions_parakeet(capsys) -> None:
    """explain_architecture should describe the parakeet backend."""
    setup_script.explain_architecture()
    output = capsys.readouterr().out
    assert "parakeet" in output.lower()
    assert "VAD" in output


def test_check_python_version_passes(capsys) -> None:
    """check_python_version should pass on Python 3.12+."""
    assert setup_script.check_python_version() is True
    output = capsys.readouterr().out
    assert "OK" in output
