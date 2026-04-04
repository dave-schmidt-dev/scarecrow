"""Tests for scripts/report.py."""

from __future__ import annotations

import json
import sys
from datetime import date, datetime
from pathlib import Path

# Bootstrap sys.path so we can import the script module
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from report import (  # noqa: E402
    _fmt_duration,
    _week_label,
    _week_range,
    extract_action_items,
    extract_first_summary_para,
    find_sessions,
    format_report,
    read_session_meta,
)

# ---------------------------------------------------------------------------
# _fmt_duration
# ---------------------------------------------------------------------------


def test_fmt_duration_zero() -> None:
    assert _fmt_duration(0) == "0s"


def test_fmt_duration_sub_minute() -> None:
    assert _fmt_duration(45) == "45s"


def test_fmt_duration_minutes() -> None:
    assert _fmt_duration(2520) == "42 min"


def test_fmt_duration_hours() -> None:
    assert _fmt_duration(3900) == "1h 5m"


def test_fmt_duration_exactly_one_hour() -> None:
    assert _fmt_duration(3600) == "1h 0m"


# ---------------------------------------------------------------------------
# _week_range / _week_label
# ---------------------------------------------------------------------------


def test_week_range_returns_monday_and_sunday() -> None:
    monday, sunday = _week_range("2026-W14")
    assert monday.weekday() == 0  # Monday
    assert sunday.weekday() == 6  # Sunday
    assert (sunday - monday).days == 6


def test_week_label_contains_year() -> None:
    monday, sunday = _week_range("2026-W14")
    label = _week_label(monday, sunday)
    assert "2026" in label
    assert "14" in label


# ---------------------------------------------------------------------------
# find_sessions
# ---------------------------------------------------------------------------


def _make_session_dir(base: Path, name: str) -> Path:
    d = base / name
    d.mkdir(parents=True)
    return d


def test_find_sessions_filters_by_date(tmp_path: Path) -> None:
    _make_session_dir(tmp_path, "2026-04-01_10-00-00_alpha")
    _make_session_dir(tmp_path, "2026-04-02_10-00-00_beta")
    _make_session_dir(tmp_path, "2026-04-03_10-00-00_gamma")

    results = find_sessions(tmp_path, date(2026, 4, 1), date(2026, 4, 2))
    names = [d.name for d in results]
    assert any("alpha" in n for n in names)
    assert any("beta" in n for n in names)
    assert not any("gamma" in n for n in names)


def test_find_sessions_excludes_non_matching_dirs(tmp_path: Path) -> None:
    (tmp_path / "not-a-session").mkdir()
    (tmp_path / "2026-04-01_10-00-00").mkdir()

    results = find_sessions(tmp_path, date(2026, 4, 1), date(2026, 4, 1))
    assert len(results) == 1


# ---------------------------------------------------------------------------
# read_session_meta
# ---------------------------------------------------------------------------


def _write_transcript(session_dir: Path, events: list[dict]) -> None:
    t = session_dir / "transcript.jsonl"
    t.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")


def test_read_session_meta_normal(tmp_path: Path) -> None:
    _write_transcript(
        tmp_path,
        [
            {"type": "session_start", "timestamp": "2026-04-03T10:00:00"},
            {"type": "session_metrics", "elapsed": 2520, "word_count": 500},
            {"type": "session_end", "timestamp": "2026-04-03T10:42:00"},
        ],
    )
    meta = read_session_meta(tmp_path)
    assert meta is not None
    assert meta["elapsed_seconds"] == 2520
    assert meta["word_count"] == 500
    assert isinstance(meta["start_dt"], datetime)


def test_read_session_meta_crash_fallback(tmp_path: Path) -> None:
    """When session_metrics is absent, fall back to end-start timestamps."""
    _write_transcript(
        tmp_path,
        [
            {"type": "session_start", "timestamp": "2026-04-03T10:00:00"},
            {"type": "transcript", "text": "Hello"},
            {"type": "session_end", "timestamp": "2026-04-03T10:30:00"},
        ],
    )
    meta = read_session_meta(tmp_path)
    assert meta is not None
    # 30 minutes = 1800 seconds
    assert meta["elapsed_seconds"] == 1800


def test_read_session_meta_no_transcript(tmp_path: Path) -> None:
    meta = read_session_meta(tmp_path)
    assert meta is None


def test_read_session_meta_slug_from_dirname(tmp_path: Path) -> None:
    session_dir = tmp_path / "2026-04-03_10-00-00_my-meeting"
    session_dir.mkdir()
    _write_transcript(
        session_dir,
        [{"type": "session_start", "timestamp": "2026-04-03T10:00:00"}],
    )
    meta = read_session_meta(session_dir)
    assert meta is not None
    assert "my-meeting" in meta["slug"]


# ---------------------------------------------------------------------------
# extract_action_items
# ---------------------------------------------------------------------------


def test_extract_action_items_returns_checkboxes(tmp_path: Path) -> None:
    summary = tmp_path / "summary.md"
    summary.write_text(
        "## Summary\nSome text.\n\n## Action Items\n"
        "- [ ] Do thing A\n- [ ] Do thing B\n\n## Key Points\n- point\n",
        encoding="utf-8",
    )
    items = extract_action_items(summary)
    assert len(items) == 2
    assert "Do thing A" in items[0]
    assert "Do thing B" in items[1]


def test_extract_action_items_no_section(tmp_path: Path) -> None:
    summary = tmp_path / "summary.md"
    summary.write_text(
        "## Summary\nNo tasks here.\n\n## Key Points\n- x\n", encoding="utf-8"
    )
    assert extract_action_items(summary) == []


def test_extract_action_items_missing_file(tmp_path: Path) -> None:
    assert extract_action_items(tmp_path / "nonexistent.md") == []


# ---------------------------------------------------------------------------
# extract_first_summary_para
# ---------------------------------------------------------------------------


def test_extract_first_summary_para_returns_first_paragraph(tmp_path: Path) -> None:
    summary = tmp_path / "summary.md"
    summary.write_text(
        "## Summary\n\nThis is the first paragraph."
        "\n\nThis is the second.\n\n## Key Points\n",
        encoding="utf-8",
    )
    para = extract_first_summary_para(summary)
    assert para == "This is the first paragraph."


def test_extract_first_summary_para_truncates(tmp_path: Path) -> None:
    summary = tmp_path / "summary.md"
    long_text = "x" * 400
    summary.write_text(f"## Summary\n\n{long_text}\n", encoding="utf-8")
    para = extract_first_summary_para(summary)
    assert len(para) <= 300
    assert para.endswith("…")


def test_extract_first_summary_para_missing_file(tmp_path: Path) -> None:
    assert extract_first_summary_para(tmp_path / "nonexistent.md") == ""


# ---------------------------------------------------------------------------
# format_report
# ---------------------------------------------------------------------------


def test_format_report_produces_markdown(tmp_path: Path) -> None:
    session_dir = tmp_path / "2026-04-03_10-00-00_standup"
    session_dir.mkdir()
    meta = {
        "dir": session_dir,
        "start_dt": datetime(2026, 4, 3, 10, 0, 0),
        "elapsed_seconds": 900,
        "word_count": 200,
        "slug": "standup",
    }
    content = format_report(
        {date(2026, 4, 3): [meta]}, "Friday, 2026-04-03", is_daily=True
    )
    assert "# Daily Report" in content
    assert "standup" in content
    assert "15 min" in content
    assert "200" in content


def test_format_report_totals_line(tmp_path: Path) -> None:
    session_dir = tmp_path / "s"
    session_dir.mkdir()
    meta = {
        "dir": session_dir,
        "start_dt": datetime(2026, 4, 3, 10, 0, 0),
        "elapsed_seconds": 2520,
        "word_count": 500,
        "slug": "",
    }
    content = format_report({date(2026, 4, 3): [meta]}, "test", is_daily=True)
    assert "1 session" in content
    assert "42 min" in content
    assert "500" in content
