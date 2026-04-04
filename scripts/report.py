#!/usr/bin/env python3
"""Generate daily or weekly session report from Scarecrow recordings.

Usage:
    python3 scripts/report.py                    # this week (default)
    python3 scripts/report.py --today
    python3 scripts/report.py --day 2026-04-03
    python3 scripts/report.py --this-week
    python3 scripts/report.py --week 2026-W14
    python3 scripts/report.py --output /tmp/report.md
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import re
import shutil
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Date utilities
# ---------------------------------------------------------------------------


def _week_range(week_str: str) -> tuple[date, date]:
    """Parse 'YYYY-WNN' → (monday, sunday) of that ISO week."""
    year, w = week_str.split("-W")
    iso = datetime.strptime(f"{year}-W{w}-1", "%G-W%V-%u")
    monday = iso.date()
    sunday = monday + timedelta(days=6)
    return monday, sunday


def _this_week() -> tuple[date, date]:
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    sunday = monday + timedelta(days=6)
    return monday, sunday


def _week_label(start: date, end: date) -> str:
    iso = start.isocalendar()
    return (
        f"Week {iso[1]}, {iso[0]} ({start.strftime('%b %-d')}-{end.strftime('%b %-d')})"
    )


# ---------------------------------------------------------------------------
# Session discovery
# ---------------------------------------------------------------------------


def find_sessions(recordings_dir: Path, start: date, end: date) -> list[Path]:
    """Return session directories whose date falls within [start, end]."""
    dirs = []
    for d in sorted(recordings_dir.iterdir()):
        if not d.is_dir():
            continue
        name = d.name
        # Match YYYY-MM-DD prefix
        m = re.match(r"^(\d{4}-\d{2}-\d{2})_", name)
        if not m:
            continue
        try:
            sess_date = date.fromisoformat(m.group(1))
        except ValueError:
            continue
        if start <= sess_date <= end:
            dirs.append(d)
    return dirs


# ---------------------------------------------------------------------------
# Session metadata extraction
# ---------------------------------------------------------------------------


def read_session_meta(session_dir: Path) -> dict | None:
    """Read transcript.jsonl and return session metadata dict.

    Returns None if the directory has no transcript.
    """
    transcript = session_dir / "transcript.jsonl"
    if not transcript.exists():
        return None

    start_dt: datetime | None = None
    end_dt: datetime | None = None
    elapsed_seconds: int = 0
    word_count: int = 0

    try:
        with transcript.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ev_type = ev.get("type", "")
                if ev_type == "session_start":
                    ts = ev.get("timestamp", "")
                    if ts:
                        with _suppress():
                            start_dt = datetime.fromisoformat(ts)
                elif ev_type == "session_metrics":
                    elapsed_seconds = int(ev.get("elapsed", 0))
                    word_count = int(ev.get("word_count", 0))
                elif ev_type == "session_end":
                    ts = ev.get("timestamp", "")
                    if ts:
                        with _suppress():
                            end_dt = datetime.fromisoformat(ts)
    except OSError:
        log.warning("Could not read %s", transcript)
        return None

    if start_dt is None:
        return None

    # Crash-safe duration fallback
    if elapsed_seconds == 0 and end_dt is not None:
        elapsed_seconds = int((end_dt - start_dt).total_seconds())
    if elapsed_seconds == 0:
        # Last resort: use directory mtime
        elapsed_seconds = int(session_dir.stat().st_mtime - start_dt.timestamp())

    # Parse slug from directory name
    name_part = session_dir.name[len("YYYY-MM-DD_HH-MM-SS_") :]
    slug = name_part if len(session_dir.name) > 19 else ""

    return {
        "dir": session_dir,
        "start_dt": start_dt,
        "elapsed_seconds": max(0, elapsed_seconds),
        "word_count": word_count,
        "slug": slug,
    }


_suppress = contextlib.suppress


# ---------------------------------------------------------------------------
# Summary parsing
# ---------------------------------------------------------------------------


def extract_action_items(summary_md: Path) -> list[str]:
    """Return checkbox lines from the ## Action Items section."""
    if not summary_md.exists():
        return []
    try:
        text = summary_md.read_text(encoding="utf-8")
    except OSError:
        return []

    m = re.search(r"^## Action Items\n(.*?)(?=^##|\Z)", text, re.M | re.DOTALL)
    if not m:
        return []
    return [
        line.strip()
        for line in m.group(1).splitlines()
        if line.strip().startswith("- [")
    ]


def extract_first_summary_para(summary_md: Path) -> str:
    """Return first non-empty paragraph from the ## Summary section (≤300 chars)."""
    if not summary_md.exists():
        return ""
    try:
        text = summary_md.read_text(encoding="utf-8")
    except OSError:
        return ""

    m = re.search(r"^## Summary\n(.*?)(?=^##|\Z)", text, re.M | re.DOTALL)
    if not m:
        return ""
    for para in m.group(1).split("\n\n"):
        para = para.strip()
        if para and not para.startswith("*"):
            if len(para) > 300:
                para = para[:297] + "…"
            return para
    return ""


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------


def _fmt_duration(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s" if seconds > 0 else "0s"
    h, m = divmod(seconds // 60, 60)
    return f"{h}h {m}m" if h else f"{m} min"


def format_report(
    sessions_by_day: dict[date, list[dict]],
    period_label: str,
    is_daily: bool = False,
) -> str:
    heading = "Daily Report" if is_daily else "Weekly Report"
    lines = [f"# {heading}: {period_label}", ""]

    total_sessions = 0
    total_elapsed = 0
    total_words = 0

    for day in sorted(sessions_by_day):
        day_sessions = sessions_by_day[day]
        if not day_sessions:
            continue
        lines.append(f"## {day.strftime('%A, %Y-%m-%d')}")
        lines.append("")
        for meta in day_sessions:
            start_dt: datetime = meta["start_dt"]
            elapsed: int = meta["elapsed_seconds"]
            words: int = meta["word_count"]
            slug: str = meta["slug"]
            session_dir: Path = meta["dir"]

            title = slug.replace("-", " ").replace("_", " ").strip() if slug else ""
            dur_str = _fmt_duration(elapsed)
            words_str = f"{words:,}" if words else "?"
            time_str = start_dt.strftime("%H:%M")

            if title:
                heading_line = (
                    f"### [{time_str}] {title} - {dur_str} - {words_str} words"
                )
            else:
                heading_line = f"### [{time_str}] {dur_str} - {words_str} words"
            lines.append(heading_line)

            para = extract_first_summary_para(session_dir / "summary.md")
            if para:
                lines.append(f"> {para}")
                lines.append("")

            items = extract_action_items(session_dir / "summary.md")
            if items:
                lines.append("**Action Items**")
                lines.extend(items)
                lines.append("")

            total_sessions += 1
            total_elapsed += elapsed
            total_words += words

    lines.append("---")
    words_str = f"{total_words:,}" if total_words else "0"
    lines.append(
        f"*{total_sessions} session{'s' if total_sessions != 1 else ''}"
        f" · {_fmt_duration(total_elapsed)}"
        f" · {words_str} words*"
    )
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def save_report(content: str, output_path: Path, obsidian_dir: Path | None) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content, encoding="utf-8")
    log.info("Report written to %s", output_path)

    if obsidian_dir is None or not obsidian_dir.is_dir():
        return
    reports_dir = obsidian_dir / "Reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    dest = reports_dir / output_path.name
    try:
        shutil.copy2(output_path, dest)
        log.info("Report synced to Obsidian: %s", dest)
    except OSError:
        log.warning("Failed to sync report to Obsidian", exc_info=True)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    parser = argparse.ArgumentParser(description="Generate Scarecrow session report")
    period = parser.add_mutually_exclusive_group()
    period.add_argument("--today", action="store_true", help="Today only")
    period.add_argument("--day", metavar="YYYY-MM-DD", help="A specific day")
    period.add_argument(
        "--this-week", action="store_true", help="Current week (default)"
    )
    period.add_argument("--week", metavar="YYYY-WNN", help="ISO week (e.g. 2026-W14)")
    parser.add_argument("--output", metavar="PATH", help="Override output file path")
    args = parser.parse_args()

    from scarecrow import config as cfg

    recordings_dir = cfg.config.DEFAULT_RECORDINGS_DIR
    obsidian_dir = cfg.config.OBSIDIAN_VAULT_DIR

    # Determine date range
    is_daily = False
    if args.today:
        today = date.today()
        start, end = today, today
        label = today.strftime("%A, %Y-%m-%d")
        filename = f"report_{today.isoformat()}.md"
        is_daily = True
    elif args.day:
        try:
            day = date.fromisoformat(args.day)
        except ValueError:
            print(f"Invalid date: {args.day}", file=sys.stderr)
            return 1
        start, end = day, day
        label = day.strftime("%A, %Y-%m-%d")
        filename = f"report_{day.isoformat()}.md"
        is_daily = True
    elif args.week:
        try:
            start, end = _week_range(args.week)
        except (ValueError, AttributeError):
            print(
                f"Invalid week format: {args.week} (expected YYYY-WNN)",
                file=sys.stderr,
            )
            return 1
        label = _week_label(start, end)
        iso = start.isocalendar()
        filename = f"report_{iso[0]}-W{iso[1]:02d}.md"
    else:
        # Default: this week
        start, end = _this_week()
        label = _week_label(start, end)
        iso = start.isocalendar()
        filename = f"report_{iso[0]}-W{iso[1]:02d}.md"

    sessions = find_sessions(recordings_dir, start, end)
    if not sessions:
        print(f"No sessions found for {label}")
        return 0

    # Group by day
    sessions_by_day: dict[date, list[dict]] = {}
    for sess_dir in sessions:
        meta = read_session_meta(sess_dir)
        if meta is None:
            continue
        day = meta["start_dt"].date()
        sessions_by_day.setdefault(day, []).append(meta)

    if not sessions_by_day:
        print(f"No readable sessions for {label}")
        return 0

    content = format_report(sessions_by_day, label, is_daily=is_daily)

    output_path = Path(args.output) if args.output else recordings_dir / filename

    save_report(content, output_path, obsidian_dir)
    print(content)
    return 0


if __name__ == "__main__":
    sys.exit(main())
