"""Tests for the shell-based suite runner."""

from __future__ import annotations

from pathlib import Path


def test_runner_script_runs_isolated_processes_for_files() -> None:
    script = Path("scripts/run_test_suite.sh").read_text(encoding="utf-8")
    assert "env -i" in script
    assert "run_pytest_file.py" in script
    assert "tests/test_app.py" in script
    assert "tests/test_behavioral.py" in script
    assert "tests/test_setup.py" in script
    assert "tests/test_transcriber.py" in script
    assert "tests/test_suite_runner.py" in script
    # Parallel execution: files run as background jobs
    assert "PIDS" in script
    assert "wait" in script
    # No SIGSEGV suppression — segfaults are real failures
    assert "139" not in script


def test_pre_push_uses_shell_test_runner() -> None:
    config = Path(".pre-commit-config.yaml").read_text(encoding="utf-8")
    assert "bash scripts/run_test_suite.sh" in config
    # Tests run on pre-push, not pre-commit (lint-only on commit)
    assert "pre-push" in config


def test_conftest_registers_portaudio_cleanup() -> None:
    conftest = Path("tests/conftest.py").read_text(encoding="utf-8")
    assert "atexit" in conftest
    assert "_terminate" in conftest
    assert "sounddevice" in conftest
