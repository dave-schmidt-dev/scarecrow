"""Tests for the stable shell-based suite runner."""

from __future__ import annotations

from pathlib import Path


def test_runner_script_groups_app_and_behavioral_tests() -> None:
    script = Path("scripts/run_test_suite.sh").read_text(encoding="utf-8")
    assert "tests/test_app.py tests/test_behavioral.py" in script
    assert "tests/test_transcriber.py" in script
    assert "tests/test_suite_runner.py" in script


def test_pre_commit_uses_shell_test_runner() -> None:
    config = Path(".pre-commit-config.yaml").read_text(encoding="utf-8")
    assert "bash scripts/run_test_suite.sh" in config
