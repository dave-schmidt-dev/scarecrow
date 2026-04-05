#!/usr/bin/env python3
"""Discard brief recording sessions that clutter the recordings directory.

Moves sessions with fewer than 200 words to .discarded/ (recoverable).
Sessions with action items are protected unless --force is used.

Usage:
    python3 scripts/cleanup.py                     # this week (default)
    python3 scripts/cleanup.py --week 2026-W14
    python3 scripts/cleanup.py --day 2026-04-03
    python3 scripts/cleanup.py --all                # all sessions
    python3 scripts/cleanup.py --dry-run            # list without moving
"""

from __future__ import annotations

import argparse
import shutil
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from report import (  # noqa: E402
    _NOTABLE_WORD_THRESHOLD,
    _week_range,
    extract_action_items,
    extract_transcript_preview,
    find_sessions,
    read_session_meta,
)


def _this_week() -> tuple[date, date]:
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    sunday = monday + timedelta(days=6)
    return monday, sunday


def _fmt_duration(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s" if seconds > 0 else "0s"
    h, m = divmod(seconds // 60, 60)
    return f"{h}h {m}m" if h else f"{m}m"


def main() -> int:
    parser = argparse.ArgumentParser(description="Discard brief recording sessions")
    period = parser.add_mutually_exclusive_group()
    period.add_argument("--week", metavar="YYYY-WNN", help="ISO week")
    period.add_argument("--day", metavar="YYYY-MM-DD", help="Specific day")
    period.add_argument("--all", action="store_true", help="All sessions ever")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List candidates without discarding",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Include sessions that have action items",
    )
    parser.add_argument(
        "--threshold",
        type=int,
        default=_NOTABLE_WORD_THRESHOLD,
        metavar="N",
        help=f"Word count threshold (default: {_NOTABLE_WORD_THRESHOLD})",
    )
    parser.add_argument(
        "--no-summary",
        action="store_true",
        help="Discard sessions that have no summary.md (quick-quit sessions)",
    )
    args = parser.parse_args()

    from scarecrow import config as cfg

    recordings_dir = cfg.config.DEFAULT_RECORDINGS_DIR

    if args.all:
        start = date(2000, 1, 1)
        end = date(2099, 12, 31)
    elif args.week:
        try:
            start, end = _week_range(args.week)
        except (ValueError, AttributeError):
            print(f"Invalid week: {args.week}", file=sys.stderr)
            return 1
    elif args.day:
        try:
            day = date.fromisoformat(args.day)
        except ValueError:
            print(f"Invalid date: {args.day}", file=sys.stderr)
            return 1
        start, end = day, day
    else:
        start, end = _this_week()

    sessions = find_sessions(recordings_dir, start, end)
    if not sessions:
        print("No sessions found.")
        return 0

    # Find discard candidates
    candidates: list[dict] = []
    protected = 0
    for sess_dir in sessions:
        meta = read_session_meta(sess_dir)
        if meta is None:
            continue

        has_summary = (sess_dir / "summary.md").exists()
        below_threshold = meta["word_count"] < args.threshold

        # Match if below word threshold OR if --no-summary and no summary.md
        if not below_threshold and not (args.no_summary and not has_summary):
            continue

        # Protect sessions with action items unless --force
        if not args.force:
            items = extract_action_items(sess_dir / "summary.md")
            if items:
                protected += 1
                continue

        preview = extract_transcript_preview(sess_dir)
        meta["preview"] = f'"{preview}"' if preview else "(no speech)"
        meta["reason"] = "no summary" if not below_threshold else "brief"
        candidates.append(meta)

    if not candidates:
        print("No sessions to discard.")
        if protected:
            print(f"  ({protected} session(s) protected — have action items)")
        return 0

    # Display candidates
    brief_count = sum(1 for m in candidates if m["reason"] == "brief")
    nosummary_count = sum(1 for m in candidates if m["reason"] == "no summary")
    parts = []
    if brief_count:
        parts.append(f"{brief_count} brief (<{args.threshold} words)")
    if nosummary_count:
        parts.append(f"{nosummary_count} without summary")
    print(f"Found {len(candidates)} sessions to discard ({', '.join(parts)}):")
    print()
    total_size = 0
    for meta in candidates:
        start_dt: datetime = meta["start_dt"]
        day_str = start_dt.strftime("%a %m/%d")
        time_str = start_dt.strftime("%H:%M")
        dur = _fmt_duration(meta["elapsed_seconds"])
        words = meta["word_count"]
        slug = meta["slug"]
        preview = meta["preview"]
        tag = " [no summary]" if meta["reason"] == "no summary" else ""

        label = slug.replace("-", " ") if slug else ""
        if label:
            print(f"  {day_str} {time_str}  {dur:>5}  {words:>4}w  {label}{tag}")
        else:
            print(f"  {day_str} {time_str}  {dur:>5}  {words:>4}w  {preview}{tag}")

        # Approximate disk usage
        for f in meta["dir"].iterdir():
            if f.is_file():
                total_size += f.stat().st_size

    print()
    size_mb = total_size / (1024 * 1024)
    if protected:
        print(f"  ({protected} brief session(s) protected — have action items)")
    print(f"  {size_mb:.0f} MB would be freed")
    print()

    if args.dry_run:
        print("Dry run — no sessions moved. Use without --dry-run to discard.")
        return 0

    # Confirm
    try:
        answer = input(f"Move {len(candidates)} sessions to .discarded/? [y/N] ")
    except (EOFError, KeyboardInterrupt):
        print("\nCancelled.")
        return 0

    if answer.strip().lower() != "y":
        print("Cancelled.")
        return 0

    # Move to .discarded/
    discarded_dir = recordings_dir / ".discarded"
    discarded_dir.mkdir(exist_ok=True)
    moved = 0
    for meta in candidates:
        src = meta["dir"]
        dest = discarded_dir / src.name
        try:
            shutil.move(str(src), str(dest))
            moved += 1
        except OSError as e:
            print(f"  Failed to move {src.name}: {e}", file=sys.stderr)

    print(f"Moved {moved} session(s) to .discarded/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
