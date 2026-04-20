"""Tests for the interactive setup helper."""

from __future__ import annotations

import importlib.util
import subprocess
import sys

from scripts import setup as setup_script

# ---------------------------------------------------------------------------
# Core dependency smoke tests — these catch deps that accidentally become
# optional or get dropped from pyproject.toml.
# ---------------------------------------------------------------------------

CORE_IMPORT_MODULES = [
    "textual",
    "sounddevice",
    "soundfile",
    "numpy",
    "llama_cpp",
    "pyannote.audio",
]

# Importing MLX-backed modules can abort the interpreter at import time if the
# host has no usable Metal device. For setup coverage we only need to assert
# that these packages are installed and discoverable from the environment.
CORE_DISCOVERABLE_MODULES = [
    "parakeet_mlx",
    "mlx_vlm",
]


class TestCoreDependenciesImportable:
    """Every core dependency must be importable in the test environment."""

    def test_core_modules_importable(self) -> None:
        failures: list[str] = []
        for mod in CORE_IMPORT_MODULES:
            result = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    (f"import importlib;importlib.import_module({mod!r})"),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode != 0:
                stderr = result.stderr.strip()
                failures.append(f"{mod}: rc={result.returncode} stderr={stderr}")
        for mod in CORE_DISCOVERABLE_MODULES:
            if importlib.util.find_spec(mod) is None:
                failures.append(f"{mod}: module spec not found")
        assert not failures, "Core deps not importable:\n" + "\n".join(failures)


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
