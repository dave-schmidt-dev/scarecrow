"""Tests for the stable shell-based suite runner."""

from __future__ import annotations

from pathlib import Path


def test_runner_script_runs_each_test_file_in_its_own_process() -> None:
    script = Path("scripts/run_test_suite.sh").read_text(encoding="utf-8")
    assert "env -i" in script
    assert "run_pytest_file.py" in script
    assert 'run_pytest "$@" tests/test_app.py' in script
    assert 'run_pytest "$@" tests/test_behavioral.py' in script
    assert "tests/test_transcriber.py" in script
    assert "tests/test_suite_runner.py" in script


def test_pre_commit_uses_shell_test_runner() -> None:
    config = Path(".pre-commit-config.yaml").read_text(encoding="utf-8")
    assert "bash scripts/run_test_suite.sh" in config
