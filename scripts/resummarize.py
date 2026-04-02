#!/usr/bin/env python3
"""Re-run summarization on an existing session directory.

Usage:
    python3 scripts/resummarize.py ~/recordings/2026-03-29_14-30-00
    python3 scripts/resummarize.py ~/recordings/2026-03-29_14-30-00 --model gemma
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

    # Simple arg parsing: positional session-dir, optional --model <name>
    args = sys.argv[1:]
    model = None
    if "--model" in args:
        idx = args.index("--model")
        if idx + 1 >= len(args):
            print("--model requires a value (e.g. gemma, nemotron)", file=sys.stderr)
            return 1
        model = args[idx + 1]
        args = args[:idx] + args[idx + 2 :]

    if len(args) != 1:
        print(
            f"Usage: {sys.argv[0]} <session-dir> [--model gemma|nemotron]",
            file=sys.stderr,
        )
        return 1

    session_dir = Path(args[0]).resolve()
    if not session_dir.is_dir():
        print(f"Not a directory: {session_dir}", file=sys.stderr)
        return 1

    transcript = session_dir / "transcript.jsonl"
    if not transcript.exists():
        print(f"No transcript.jsonl in {session_dir}", file=sys.stderr)
        return 1

    from scarecrow.config import OBSIDIAN_VAULT_DIR
    from scarecrow.summarizer import summarize_session

    # When benchmarking with --model, write to summary_<model>.md
    output_name = f"summary_{model}.md" if model else "summary.md"

    print(f"Summarizing {session_dir} (model={model or 'default'})...")
    result = summarize_session(
        session_dir,
        obsidian_dir=OBSIDIAN_VAULT_DIR,
        model=model,
        output_name=output_name,
    )
    if result:
        print(f"Summary written to {result}")
        return 0
    else:
        print("Summarization failed. Check summary.md for details.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
