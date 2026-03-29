#!/usr/bin/env python3
"""Repository policy checks used by git hooks."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
REQUIRED_DOCS = ("README.md", "HISTORY.md")
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
        print("reminder: HISTORY.md not staged with this code change.", file=sys.stderr)
    return []


def _check_bug_sections(sections: list[str]) -> list[str]:
    """Validate a list of raw bug section bodies (text after the heading marker)."""
    failures: list[str] = []
    for raw in sections:
        title, _, body = raw.partition("\n")
        # Only squashed bugs require a regression test reference.
        # Won't-fix bugs are exempt — they have no fix to regress.
        if "- Status: squashed" not in body:
            continue
        match = re.search(r"(?mi)^- Regression test:\s*(.+)$", body)
        if match is None:
            failures.append(f"{title.strip()}: missing regression test entry.")
            continue
        value = match.group(1).strip().lower()
        # A valid reference must contain a test path (tests/) or test node (::).
        # Everything else — plain English, script commands, "n/a", "none",
        # "pending", "manual", "not a formal test" — is rejected.
        has_test_ref = "::" in value or "tests/" in value
        if not has_test_ref:
            failures.append(
                f"{title.strip()}: squashed bug must name a regression test."
            )
    return failures


def check_bugs_regression_refs() -> list[str]:
    failures: list[str] = []

    # Backward compat: scan BUGS.md if it still exists (during migration).
    bugs_path = REPO_ROOT / "BUGS.md"
    if bugs_path.exists():
        text = bugs_path.read_text(encoding="utf-8")
        sections = re.split(r"(?m)^## ", text)
        failures.extend(_check_bug_sections(sections[1:]))

    # Also scan HISTORY.md for bug entries.  In HISTORY.md bugs are nested
    # under date headings, so they use h3 (### [BUG-) instead of h2.
    history_path = REPO_ROOT / "HISTORY.md"
    if history_path.exists():
        text = history_path.read_text(encoding="utf-8")
        sections = re.split(r"(?m)^### ", text)
        bug_sections = [s for s in sections[1:] if s.startswith("[BUG-")]
        failures.extend(_check_bug_sections(bug_sections))

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
