#!/usr/bin/env python3
"""Re-run summarization on an existing session directory.

Usage:
    python3 scripts/resummarize.py ~/recordings/2026-03-29_14-30-00
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <session-dir>", file=sys.stderr)
        return 1

    session_dir = Path(sys.argv[1]).resolve()
    if not session_dir.is_dir():
        print(f"Not a directory: {session_dir}", file=sys.stderr)
        return 1

    transcript = session_dir / "transcript.jsonl"
    if not transcript.exists():
        print(f"No transcript.jsonl in {session_dir}", file=sys.stderr)
        return 1

    from scarecrow.config import OBSIDIAN_VAULT_DIR
    from scarecrow.summarizer import summarize_session

    print(f"Summarizing {session_dir}...")
    result = summarize_session(session_dir, obsidian_dir=OBSIDIAN_VAULT_DIR)
    if result:
        print(f"Summary written to {result}")
        return 0
    else:
        print("Summarization failed. Check summary.md for details.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
