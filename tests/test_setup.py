"""Tests for the interactive setup helper."""

from __future__ import annotations

import importlib

from scripts import setup as setup_script

# ---------------------------------------------------------------------------
# Core dependency smoke tests — these catch deps that accidentally become
# optional or get dropped from pyproject.toml.
# ---------------------------------------------------------------------------

CORE_MODULES = [
    "textual",
    "parakeet_mlx",
    "sounddevice",
    "soundfile",
    "numpy",
    "llama_cpp",
    "mlx_vlm",
    "pyannote.audio",
]


class TestCoreDependenciesImportable:
    """Every core dependency must be importable in the test environment."""

    def test_core_modules_importable(self) -> None:
        missing = []
        for mod in CORE_MODULES:
            try:
                importlib.import_module(mod)
            except ImportError:
                missing.append(mod)
        assert not missing, f"Core deps not importable: {missing}"


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
