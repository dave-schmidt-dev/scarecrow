"""Tests for repository policy enforcement hooks."""

from __future__ import annotations

from pathlib import Path

from scripts import check_repo_policy


def test_check_history_updated_requires_history_for_code_changes() -> None:
    staged = ["scarecrow/app.py"]
    failures = check_repo_policy.check_history_updated(staged)
    assert failures == [
        "Code or behavior-affecting files are staged but HISTORY.md is not updated."
    ]


def test_check_history_updated_allows_history_when_staged() -> None:
    staged = ["scarecrow/app.py", "HISTORY.md"]
    assert check_repo_policy.check_history_updated(staged) == []


def test_check_bugs_regression_refs_rejects_pending_squash(tmp_path: Path) -> None:
    original_root = check_repo_policy.REPO_ROOT
    try:
        check_repo_policy.REPO_ROOT = tmp_path
        (tmp_path / "BUGS.md").write_text(
            "## [BUG-demo]\n- Status: squashed\n- Regression test: pending\n",
            encoding="utf-8",
        )
        failures = check_repo_policy.check_bugs_regression_refs()
    finally:
        check_repo_policy.REPO_ROOT = original_root

    assert failures == ["[BUG-demo]: squashed bug must name a regression test."]


def test_check_bugs_regression_refs_accepts_named_test(tmp_path: Path) -> None:
    original_root = check_repo_policy.REPO_ROOT
    try:
        check_repo_policy.REPO_ROOT = tmp_path
        (tmp_path / "BUGS.md").write_text(
            "## [BUG-demo]\n"
            "- Status: squashed\n"
            "- Regression test: tests/test_demo.py::test_demo\n",
            encoding="utf-8",
        )
        failures = check_repo_policy.check_bugs_regression_refs()
    finally:
        check_repo_policy.REPO_ROOT = original_root

    assert failures == []
