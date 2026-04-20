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
    _action_item_label,
    _fmt_duration,
    _is_notable,
    _last_week,
    _week_label,
    _week_range,
    extract_action_item_details,
    extract_action_items,
    extract_explicit_task_notes,
    extract_first_summary_para,
    extract_session_brief,
    find_sessions,
    format_report,
    normalize_action_item,
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


def test_last_week_returns_previous_iso_week() -> None:
    monday, sunday = _last_week()
    assert monday.weekday() == 0
    assert sunday.weekday() == 6
    assert (sunday - monday).days == 6


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
# extract_action_item_details / extract_explicit_task_notes / normalization
# ---------------------------------------------------------------------------


def test_extract_action_item_details_strips_checkbox_prefix(tmp_path: Path) -> None:
    summary = tmp_path / "summary.md"
    summary.write_text(
        "## Summary\nSome text.\n\n## Action Items\n- [ ] Follow up on budget\n",
        encoding="utf-8",
    )
    items = extract_action_item_details(summary)
    assert items == [
        {
            "raw": "- [ ] Follow up on budget",
            "text": "Follow up on budget",
            "normalized": "follow up on budget",
        }
    ]


def test_extract_explicit_task_notes_reads_task_note_events(tmp_path: Path) -> None:
    _write_transcript(
        tmp_path,
        [
            {"type": "note", "tag": "TASK", "text": "Review contract"},
            {"type": "note", "tag": "NOTE", "text": "Background only"},
        ],
    )
    items = extract_explicit_task_notes(tmp_path)
    assert items == [
        {
            "text": "Review contract",
            "normalized": "review contract",
        }
    ]


def test_normalize_action_item_collapses_case_and_punctuation() -> None:
    assert normalize_action_item("Follow-up on Budget!!!") == "follow up on budget"


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
# extract_session_brief
# ---------------------------------------------------------------------------


def test_extract_session_brief_limits_to_two_sentences(tmp_path: Path) -> None:
    summary = tmp_path / "summary.md"
    summary.write_text(
        "## Summary\n\nSentence one. Sentence two. Sentence three.\n",
        encoding="utf-8",
    )
    brief = extract_session_brief(summary)
    assert brief == "Sentence one. Sentence two."


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


# ---------------------------------------------------------------------------
# _is_notable
# ---------------------------------------------------------------------------


def test_notable_by_word_count() -> None:
    meta = {"word_count": 200}
    assert _is_notable(meta) is True


def test_brief_by_word_count() -> None:
    meta = {"word_count": 199}
    assert _is_notable(meta) is False


def test_brief_zero_words() -> None:
    meta = {"word_count": 0}
    assert _is_notable(meta) is False


# ---------------------------------------------------------------------------
# _action_item_label
# ---------------------------------------------------------------------------


def test_action_item_label_with_slug() -> None:
    meta = {
        "slug": "team-standup",
        "start_dt": datetime(2026, 4, 1, 15, 30, 0),
    }
    label = _action_item_label(meta)
    assert "team standup" in label
    assert "Wed" in label
    assert "15:30" in label


def test_action_item_label_without_slug() -> None:
    meta = {
        "slug": "",
        "start_dt": datetime(2026, 4, 1, 9, 5, 0),
    }
    label = _action_item_label(meta)
    assert "09:05 recording" in label
    assert "Wed" in label


# ---------------------------------------------------------------------------
# extract_action_items — multiple sections
# ---------------------------------------------------------------------------


def test_extract_action_items_multiple_sections(tmp_path: Path) -> None:
    """Old concatenated multi-segment summaries have multiple Action Items sections."""
    summary = tmp_path / "summary.md"
    summary.write_text(
        "# Segment 1\n\n## Summary\nFirst half.\n\n"
        "## Action Items\n- [ ] Task from seg 1\n\n---\n\n"
        "# Segment 2\n\n## Summary\nSecond half.\n\n"
        "## Action Items\n- [ ] Task from seg 2\n",
        encoding="utf-8",
    )
    items = extract_action_items(summary)
    assert len(items) == 2
    assert any("seg 1" in i for i in items)
    assert any("seg 2" in i for i in items)


# ---------------------------------------------------------------------------
# format_report — notable vs brief
# ---------------------------------------------------------------------------


def test_brief_sessions_collapsed(tmp_path: Path) -> None:
    """Sessions under the word threshold get collapsed into one line."""
    brief_dir = tmp_path / "brief"
    brief_dir.mkdir()
    sessions = []
    for i in range(5):
        d = tmp_path / f"s{i}"
        d.mkdir()
        sessions.append(
            {
                "dir": d,
                "start_dt": datetime(2026, 4, 3, 8, i, 0),
                "elapsed_seconds": 30,
                "word_count": 40,
                "slug": "",
            }
        )

    content = format_report({date(2026, 4, 3): sessions}, "test", is_daily=True)
    # Should NOT have individual ### headings for brief sessions
    assert "### [" not in content
    # Should have a collapsed line
    assert "5 brief recordings" in content


def test_action_items_consolidated(tmp_path: Path) -> None:
    """Action items appear in a consolidated section, not inline."""
    session_dir = tmp_path / "2026-04-03_10-00-00_meeting"
    session_dir.mkdir()
    summary = session_dir / "summary.md"
    summary.write_text(
        "## Summary\nStuff happened.\n\n"
        "## Action Items\n- [ ] Follow up on X\n- [ ] Review Y\n",
        encoding="utf-8",
    )
    meta = {
        "dir": session_dir,
        "start_dt": datetime(2026, 4, 3, 10, 0, 0),
        "elapsed_seconds": 1800,
        "word_count": 3000,
        "slug": "meeting",
    }
    content = format_report({date(2026, 4, 3): [meta]}, "test", is_daily=True)
    # Action items under consolidated heading
    assert "## Action Items" in content
    assert "Follow up on X" in content
    # No inline "**Action Items**" under the session heading
    assert "**Action Items**" not in content


def test_all_brief_day(tmp_path: Path) -> None:
    """Day with only brief sessions renders just a collapsed line."""
    d = tmp_path / "s"
    d.mkdir()
    meta = {
        "dir": d,
        "start_dt": datetime(2026, 4, 3, 8, 0, 0),
        "elapsed_seconds": 15,
        "word_count": 30,
        "slug": "",
    }
    content = format_report({date(2026, 4, 3): [meta]}, "test", is_daily=True)
    assert "### [" not in content
    assert "1 brief recording" in content


def test_footer_notable_brief_split(tmp_path: Path) -> None:
    """Footer shows notable/brief counts when both exist."""
    notable_dir = tmp_path / "notable"
    notable_dir.mkdir()
    brief_dir = tmp_path / "brief"
    brief_dir.mkdir()
    sessions = [
        {
            "dir": notable_dir,
            "start_dt": datetime(2026, 4, 3, 10, 0, 0),
            "elapsed_seconds": 1800,
            "word_count": 5000,
            "slug": "",
        },
        {
            "dir": brief_dir,
            "start_dt": datetime(2026, 4, 3, 8, 0, 0),
            "elapsed_seconds": 20,
            "word_count": 30,
            "slug": "",
        },
    ]
    content = format_report({date(2026, 4, 3): sessions}, "test", is_daily=True)
    assert "1 notable" in content
    assert "1 brief" in content


def test_footer_no_split_when_all_notable(tmp_path: Path) -> None:
    """Footer uses simple format when no brief sessions exist."""
    d = tmp_path / "s"
    d.mkdir()
    meta = {
        "dir": d,
        "start_dt": datetime(2026, 4, 3, 10, 0, 0),
        "elapsed_seconds": 1800,
        "word_count": 5000,
        "slug": "",
    }
    content = format_report({date(2026, 4, 3): [meta]}, "test", is_daily=True)
    assert "notable" not in content
    assert "brief" not in content
    assert "1 session" in content


def test_weekly_report_uses_requested_structure(tmp_path: Path) -> None:
    session_dir = tmp_path / "2026-04-03_10-00-00_meeting"
    session_dir.mkdir()
    _write_transcript(
        session_dir,
        [
            {"type": "session_start", "timestamp": "2026-04-03T10:00:00"},
            {"type": "note", "tag": "TASK", "text": "Send recap"},
        ],
    )
    (session_dir / "summary.md").write_text(
        "## Summary\nStuff happened.\n\n"
        "## Action Items\n- [ ] Send recap\n- [ ] Follow up with vendor\n",
        encoding="utf-8",
    )
    meta = {
        "dir": session_dir,
        "start_dt": datetime(2026, 4, 3, 10, 0, 0),
        "elapsed_seconds": 1800,
        "word_count": 500,
        "slug": "meeting",
    }

    content = format_report(
        {date(2026, 4, 3): [meta]},
        "Week 14",
        is_daily=False,
        overview_text="This week focused on one meeting.",
    )

    assert "This week focused on one meeting." in content
    assert "## Action Items" in content
    assert "## Sessions" in content
    assert content.index("## Action Items") < content.index("## Sessions")
    assert "- [ ] Send recap" in content
    assert "- **[10:00] meeting**" in content


def test_weekly_report_lists_every_session(tmp_path: Path) -> None:
    session_a = tmp_path / "2026-04-03_10-00-00_alpha"
    session_b = tmp_path / "2026-04-04_11-00-00_beta"
    session_a.mkdir()
    session_b.mkdir()
    (session_a / "summary.md").write_text(
        "## Summary\nA.\n\n## Action Items\n- [ ] Review contract\n",
        encoding="utf-8",
    )
    (session_b / "summary.md").write_text(
        "## Summary\nB.\n\n## Action Items\n- [ ] Review contract\n",
        encoding="utf-8",
    )
    sessions = {
        date(2026, 4, 3): [
            {
                "dir": session_a,
                "start_dt": datetime(2026, 4, 3, 10, 0, 0),
                "elapsed_seconds": 1800,
                "word_count": 500,
                "slug": "alpha",
            }
        ],
        date(2026, 4, 4): [
            {
                "dir": session_b,
                "start_dt": datetime(2026, 4, 4, 11, 0, 0),
                "elapsed_seconds": 1200,
                "word_count": 500,
                "slug": "beta",
            }
        ],
    }

    content = format_report(sessions, "Week 14", is_daily=False)

    assert "### Friday, 2026-04-03" in content
    assert "### Saturday, 2026-04-04" in content
    assert "- **[10:00] alpha**" in content
    assert "- **[11:00] beta**" in content
