"""Tests for repository policy enforcement hooks."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts import check_repo_policy


def test_check_history_updated_warns_but_does_not_block_code_changes(
    capsys: pytest.CaptureFixture[str],
) -> None:
    staged = ["scarecrow/app.py"]
    failures = check_repo_policy.check_history_updated(staged)
    assert failures == []
    captured = capsys.readouterr()
    assert "HISTORY.md" in captured.err


def test_check_history_updated_no_warning_when_history_staged(
    capsys: pytest.CaptureFixture[str],
) -> None:
    staged = ["scarecrow/app.py", "HISTORY.md"]
    failures = check_repo_policy.check_history_updated(staged)
    assert failures == []
    captured = capsys.readouterr()
    assert captured.err == ""


def test_check_history_updated_no_warning_for_non_code_changes(
    capsys: pytest.CaptureFixture[str],
) -> None:
    staged = ["some_other_file.txt"]
    failures = check_repo_policy.check_history_updated(staged)
    assert failures == []
    captured = capsys.readouterr()
    assert captured.err == ""


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


def test_check_bugs_regression_refs_rejects_na_substring(tmp_path: Path) -> None:
    """'n/a (not applicable)' must also be rejected, not just exact 'n/a'."""
    original_root = check_repo_policy.REPO_ROOT
    try:
        check_repo_policy.REPO_ROOT = tmp_path
        (tmp_path / "BUGS.md").write_text(
            "## [BUG-demo]\n"
            "- Status: squashed\n"
            "- Regression test: n/a (not applicable)\n",
            encoding="utf-8",
        )
        failures = check_repo_policy.check_bugs_regression_refs()
    finally:
        check_repo_policy.REPO_ROOT = original_root

    assert failures == ["[BUG-demo]: squashed bug must name a regression test."]


def test_check_bugs_regression_refs_rejects_manual_only(tmp_path: Path) -> None:
    """'manual only' regression test entries must be rejected."""
    original_root = check_repo_policy.REPO_ROOT
    try:
        check_repo_policy.REPO_ROOT = tmp_path
        (tmp_path / "BUGS.md").write_text(
            "## [BUG-demo]\n"
            "- Status: squashed\n"
            "- Regression test: manual only — not automated\n",
            encoding="utf-8",
        )
        failures = check_repo_policy.check_bugs_regression_refs()
    finally:
        check_repo_policy.REPO_ROOT = original_root

    assert failures == ["[BUG-demo]: squashed bug must name a regression test."]


def test_check_bugs_regression_refs_rejects_script_command(tmp_path: Path) -> None:
    """A script command (not a test path) must be rejected as a regression test."""
    original_root = check_repo_policy.REPO_ROOT
    try:
        check_repo_policy.REPO_ROOT = tmp_path
        (tmp_path / "BUGS.md").write_text(
            "## [BUG-demo]\n"
            "- Status: squashed\n"
            "- Regression test: scripts/check_repo_policy.py --staged-only\n",
            encoding="utf-8",
        )
        failures = check_repo_policy.check_bugs_regression_refs()
    finally:
        check_repo_policy.REPO_ROOT = original_root

    assert failures == ["[BUG-demo]: squashed bug must name a regression test."]


def test_check_bugs_regression_refs_rejects_informal_text(tmp_path: Path) -> None:
    """Informal validation prose must be rejected as a regression test."""
    original_root = check_repo_policy.REPO_ROOT
    try:
        check_repo_policy.REPO_ROOT = tmp_path
        (tmp_path / "BUGS.md").write_text(
            "## [BUG-demo]\n"
            "- Status: squashed\n"
            "- Regression test: not a formal test — validated by crash absence\n",
            encoding="utf-8",
        )
        failures = check_repo_policy.check_bugs_regression_refs()
    finally:
        check_repo_policy.REPO_ROOT = original_root

    assert failures == ["[BUG-demo]: squashed bug must name a regression test."]


def test_check_bugs_regression_refs_skips_wont_fix(tmp_path: Path) -> None:
    """Won't-fix bugs are exempt from regression test requirements."""
    original_root = check_repo_policy.REPO_ROOT
    try:
        check_repo_policy.REPO_ROOT = tmp_path
        (tmp_path / "BUGS.md").write_text(
            "## [BUG-demo]\n"
            "- Status: won't fix\n"
            "- Regression test: n/a (component removed)\n",
            encoding="utf-8",
        )
        failures = check_repo_policy.check_bugs_regression_refs()
    finally:
        check_repo_policy.REPO_ROOT = original_root

    assert failures == []


def test_required_docs_does_not_include_bugs() -> None:
    """BUGS.md is being consolidated into HISTORY.md and must not be required."""
    assert "BUGS.md" not in check_repo_policy.REQUIRED_DOCS


def test_check_bugs_regression_refs_scans_history_md(tmp_path: Path) -> None:
    """HISTORY.md bug entries (### [BUG-) are validated like BUGS.md entries."""
    original_root = check_repo_policy.REPO_ROOT
    try:
        check_repo_policy.REPO_ROOT = tmp_path
        (tmp_path / "HISTORY.md").write_text(
            "# History\n\n"
            "## 2026-03-29\n\n"
            "### [BUG-demo]\n"
            "- Status: squashed\n"
            "- Regression test: tests/test_demo.py::test_demo\n",
            encoding="utf-8",
        )
        failures = check_repo_policy.check_bugs_regression_refs()
    finally:
        check_repo_policy.REPO_ROOT = original_root

    assert failures == []


def test_check_bugs_regression_refs_history_md_rejects_pending(tmp_path: Path) -> None:
    """A squashed bug in HISTORY.md with a pending regression test is rejected."""
    original_root = check_repo_policy.REPO_ROOT
    try:
        check_repo_policy.REPO_ROOT = tmp_path
        (tmp_path / "HISTORY.md").write_text(
            "# History\n\n"
            "## 2026-03-29\n\n"
            "### [BUG-demo]\n"
            "- Status: squashed\n"
            "- Regression test: pending\n",
            encoding="utf-8",
        )
        failures = check_repo_policy.check_bugs_regression_refs()
    finally:
        check_repo_policy.REPO_ROOT = original_root

    assert failures == ["[BUG-demo]: squashed bug must name a regression test."]


def test_check_bugs_regression_refs_history_md_skips_non_bug_h3(
    tmp_path: Path,
) -> None:
    """Non-bug h3 sections in HISTORY.md are not validated as bug entries."""
    original_root = check_repo_policy.REPO_ROOT
    try:
        check_repo_policy.REPO_ROOT = tmp_path
        (tmp_path / "HISTORY.md").write_text(
            "# History\n\n## 2026-03-29\n\n### Added\n- Some new feature\n",
            encoding="utf-8",
        )
        failures = check_repo_policy.check_bugs_regression_refs()
    finally:
        check_repo_policy.REPO_ROOT = original_root

    assert failures == []
