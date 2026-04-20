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


def extract_transcript_preview(session_dir: Path, max_chars: int = 200) -> str:
    """Return the first ~max_chars of transcribed speech for context."""
    transcript = session_dir / "transcript.jsonl"
    if not transcript.exists():
        return ""
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
                if ev.get("type") == "transcript":
                    text = ev.get("text", "").strip()
                    if text:
                        if len(text) > max_chars:
                            return text[:max_chars] + "\u2026"
                        return text
    except OSError:
        pass
    return ""


def extract_action_items(summary_md: Path) -> list[str]:
    """Return checkbox lines from all ## Action Items sections."""
    return [item["text"] for item in extract_action_item_details(summary_md)]


def extract_action_item_details(summary_md: Path) -> list[dict[str, str]]:
    """Return action items with normalized text for matching and deduping."""
    if not summary_md.exists():
        return []
    try:
        text = summary_md.read_text(encoding="utf-8")
    except OSError:
        return []

    items: list[dict[str, str]] = []
    for m in re.finditer(r"^## Action Items\n(.*?)(?=^##|\Z)", text, re.M | re.DOTALL):
        for line in m.group(1).splitlines():
            stripped = line.strip()
            if not stripped.startswith("- ["):
                continue
            item_text = re.sub(r"^- \[[ xX]\]\s*", "", stripped).strip()
            normalized = normalize_action_item(item_text)
            items.append(
                {
                    "raw": stripped,
                    "text": item_text,
                    "normalized": normalized,
                }
            )
    return items


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


def extract_explicit_task_notes(session_dir: Path) -> list[dict[str, str]]:
    """Return explicit TASK notes from transcript.jsonl for a session."""
    transcript = session_dir / "transcript.jsonl"
    if not transcript.exists():
        return []

    items: list[dict[str, str]] = []
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
                if ev.get("type") != "note":
                    continue
                if ev.get("tag", "").upper() != "TASK":
                    continue
                text = ev.get("text", "").strip()
                if not text:
                    continue
                items.append(
                    {
                        "text": text,
                        "normalized": normalize_action_item(text),
                    }
                )
    except OSError:
        return []

    return items


def normalize_action_item(text: str) -> str:
    """Normalize action item text for exact weekly matching."""
    normalized = text.casefold().strip()
    normalized = re.sub(r"^[\-\*\d\.\)\s]+", "", normalized)
    normalized = re.sub(r"[^\w\s]", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip()


def collect_follow_up_signals(
    sessions_by_day: dict[date, list[dict]],
) -> tuple[list[tuple[str, str]], list[tuple[str, list[str]]]]:
    """Return inferred and repeated follow-up signals for weekly reporting.

    Inferred follow-ups are action items present in summary.md but absent from
    explicit TASK notes in transcript.jsonl. Repeated follow-ups are exact
    normalized matches that appear in multiple sessions during the week.
    """
    inferred: list[tuple[str, str]] = []
    repeated_sources: dict[str, list[str]] = {}
    repeated_texts: dict[str, str] = {}

    for day in sorted(sessions_by_day):
        for meta in sessions_by_day[day]:
            label = _action_item_label(meta)
            explicit = {
                item["normalized"]
                for item in extract_explicit_task_notes(meta["dir"])
                if item["normalized"]
            }
            summary_items = extract_action_item_details(meta["dir"] / "summary.md")
            session_seen: set[str] = set()
            for item in summary_items:
                normalized = item["normalized"]
                if not normalized:
                    continue
                if normalized not in explicit:
                    inferred.append((label, item["text"]))
                if normalized in session_seen:
                    continue
                session_seen.add(normalized)
                repeated_sources.setdefault(normalized, []).append(label)
                repeated_texts.setdefault(normalized, item["text"])

    repeated: list[tuple[str, list[str]]] = []
    for normalized, labels in repeated_sources.items():
        unique_labels = list(dict.fromkeys(labels))
        if len(unique_labels) < 2:
            continue
        repeated.append((repeated_texts[normalized], unique_labels))

    repeated.sort(key=lambda entry: (-len(entry[1]), entry[0].casefold()))
    inferred.sort(key=lambda entry: (entry[0], entry[1].casefold()))
    return inferred, repeated


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------


def _fmt_duration(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s" if seconds > 0 else "0s"
    h, m = divmod(seconds // 60, 60)
    return f"{h}h {m}m" if h else f"{m} min"


_NOTABLE_WORD_THRESHOLD = 200


def _is_notable(meta: dict) -> bool:
    """A session is notable if it captured enough speech to be meaningful."""
    return meta["word_count"] >= _NOTABLE_WORD_THRESHOLD


def _action_item_label(meta: dict) -> str:
    """Return a bold label attributing action items to a session."""
    slug: str = meta["slug"]
    start_dt: datetime = meta["start_dt"]
    day_abbrev = start_dt.strftime("%a")
    time_str = start_dt.strftime("%H:%M")
    if slug:
        title = slug.replace("-", " ").replace("_", " ").strip()
        return f"**{title}** ({day_abbrev} {time_str})"
    return f"**{time_str} recording** ({day_abbrev})"


def format_report(
    sessions_by_day: dict[date, list[dict]],
    period_label: str,
    is_daily: bool = False,
) -> str:
    heading = "Daily Report" if is_daily else "Weekly Report"
    lines = [f"# {heading}: {period_label}", ""]

    total_sessions = 0
    total_notable = 0
    total_brief = 0
    total_elapsed = 0
    total_words = 0

    # Collect action items across all sessions for consolidated section
    all_action_items: list[tuple[str, list[str]]] = []
    inferred_followups, repeated_followups = collect_follow_up_signals(sessions_by_day)

    for day in sorted(sessions_by_day):
        day_sessions = sessions_by_day[day]
        if not day_sessions:
            continue

        lines.append(f"## {day.strftime('%A, %Y-%m-%d')}")
        lines.append("")

        notable: list[dict] = []
        brief_count = 0
        brief_elapsed = 0
        brief_words = 0

        for meta in day_sessions:
            total_sessions += 1
            total_elapsed += meta["elapsed_seconds"]
            total_words += meta["word_count"]

            # Collect action items from every session
            items = extract_action_items(meta["dir"] / "summary.md")
            if items:
                all_action_items.append((_action_item_label(meta), items))

            if _is_notable(meta):
                notable.append(meta)
                total_notable += 1
            else:
                brief_count += 1
                brief_elapsed += meta["elapsed_seconds"]
                brief_words += meta["word_count"]
                total_brief += 1

        # Render notable sessions
        for meta in notable:
            start_dt: datetime = meta["start_dt"]
            elapsed: int = meta["elapsed_seconds"]
            words: int = meta["word_count"]
            slug: str = meta["slug"]

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

            para = extract_first_summary_para(meta["dir"] / "summary.md")
            if not para:
                para = extract_transcript_preview(meta["dir"])
            if para:
                lines.append(f"> {para}")
            lines.append("")

        # Collapse brief sessions into one line
        if brief_count > 0:
            noun = "recording" if brief_count == 1 else "recordings"
            brief_w = f"{brief_words:,}" if brief_words else "0"
            lines.append(
                f"*+ {brief_count} brief {noun}"
                f" \u00b7 {_fmt_duration(brief_elapsed)}"
                f" \u00b7 {brief_w} words*"
            )
            lines.append("")

    # Consolidated Action Items section
    if not is_daily and (inferred_followups or repeated_followups):
        lines.append("## Follow-Up Radar")
        lines.append("")
        lines.append(
            "*Heuristics only: likely dropped follow-ups are inferred commitments "
            "and repeated items that resurfaced across multiple sessions.*"
        )
        lines.append("")
        if inferred_followups:
            lines.append("### Inferred Follow-Ups")
            lines.append("")
            for label, item_text in inferred_followups:
                lines.append(f"- {item_text} ({label})")
            lines.append("")
        if repeated_followups:
            lines.append("### Repeated Follow-Ups")
            lines.append("")
            for item_text, labels in repeated_followups:
                labels_str = ", ".join(labels)
                lines.append(f"- {item_text} ({len(labels)} sessions: {labels_str})")
            lines.append("")

    if all_action_items:
        lines.append("## Action Items")
        lines.append("")
        for label, items in all_action_items:
            lines.append(label)
            lines.extend(items)
            lines.append("")

    # Footer
    lines.append("---")
    words_str = f"{total_words:,}" if total_words else "0"
    if total_brief > 0:
        lines.append(
            f"*{total_sessions}"
            f" session{'s' if total_sessions != 1 else ''}"
            f" ({total_notable} notable \u00b7 {total_brief} brief)"
            f" \u00b7 {_fmt_duration(total_elapsed)}"
            f" \u00b7 {words_str} words*"
        )
    else:
        lines.append(
            f"*{total_sessions}"
            f" session{'s' if total_sessions != 1 else ''}"
            f" \u00b7 {_fmt_duration(total_elapsed)}"
            f" \u00b7 {words_str} words*"
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
