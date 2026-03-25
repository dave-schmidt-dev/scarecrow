#!/usr/bin/env python3
"""Repository policy checks used by git hooks."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
REQUIRED_DOCS = ("README.md", "HISTORY.md", "BUGS.md")
CODE_PREFIXES = ("scarecrow/", "tests/", "scripts/")
CODE_SUFFIXES = (".py", ".tcss", ".toml", ".yaml", ".yml", ".json", ".md")


def _git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def _staged_files() -> list[str]:
    output = _git("diff", "--cached", "--name-only", "--diff-filter=ACMR")
    return [line.strip() for line in output.splitlines() if line.strip()]


def _is_code_change(path: str) -> bool:
    if path in REQUIRED_DOCS:
        return False
    return path.startswith(CODE_PREFIXES) or path.endswith(CODE_SUFFIXES)


def check_required_docs() -> list[str]:
    failures: list[str] = []
    for doc in REQUIRED_DOCS:
        if not (REPO_ROOT / doc).exists():
            failures.append(f"Missing required project doc: {doc}")
    return failures


def check_history_updated(staged_files: list[str]) -> list[str]:
    if "HISTORY.md" in staged_files:
        return []
    if any(_is_code_change(path) for path in staged_files):
        return [
            "Code or behavior-affecting files are staged but HISTORY.md is not updated."
        ]
    return []


def check_bugs_regression_refs() -> list[str]:
    bugs_path = REPO_ROOT / "BUGS.md"
    if not bugs_path.exists():
        return []

    failures: list[str] = []
    text = bugs_path.read_text(encoding="utf-8")
    sections = re.split(r"(?m)^## ", text)
    for raw in sections[1:]:
        title, _, body = raw.partition("\n")
        if "- Status: squashed" not in body:
            continue
        match = re.search(r"(?mi)^- Regression test:\s*(.+)$", body)
        if match is None:
            failures.append(f"{title.strip()}: missing regression test entry.")
            continue
        value = match.group(1).strip().lower()
        if value in {"pending", "none", "n/a"} or "pending" in value or "n/a" in value:
            failures.append(
                f"{title.strip()}: squashed bug must name a regression test."
            )
    return failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--staged-only",
        action="store_true",
        help="Use staged files to decide whether HISTORY.md must be updated.",
    )
    args = parser.parse_args()

    failures: list[str] = []
    staged_files = _staged_files() if args.staged_only else []

    failures.extend(check_required_docs())
    failures.extend(check_bugs_regression_refs())
    if args.staged_only:
        failures.extend(check_history_updated(staged_files))

    if failures:
        for failure in failures:
            print(f"policy: {failure}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
