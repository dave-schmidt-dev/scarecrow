"""JSONL schema contract tests for Scarecrow transcript format.

Verifies that every event type emitted by the app conforms to the documented
JSONL schema, with all required fields present and correctly typed.

NOTE: These tests assume the standardized schema where all events have both
``timestamp`` (ISO 8601) and ``elapsed`` (int seconds).  The session_start
and session_end events are exempt from the ``elapsed`` requirement because
they record wall-clock instants rather than recording-relative positions.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# Schema definition
# ---------------------------------------------------------------------------

REQUIRED_FIELDS: dict[str, set[str]] = {
    "session_start": {"type", "timestamp", "session_dir"},
    "session_end": {"type", "timestamp"},
    "transcript": {"type", "elapsed", "timestamp", "text"},
    "divider": {"type", "elapsed", "timestamp"},
    "pause": {"type", "elapsed", "timestamp"},
    "resume": {"type", "elapsed", "timestamp"},
    "note": {"type", "tag", "elapsed", "timestamp", "text"},
    "warning": {"type", "elapsed", "timestamp", "text"},
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read_jsonl(path: Path) -> list[dict]:
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    return [json.loads(line) for line in lines]


def _validate_event(event: dict) -> list[str]:
    """Return a list of schema violations for an event dict."""
    violations: list[str] = []
    etype = event.get("type")
    if etype is None:
        return ["missing 'type' field"]
    if etype not in REQUIRED_FIELDS:
        return [f"unknown event type: {etype!r}"]
    required = REQUIRED_FIELDS[etype]
    missing = required - set(event.keys())
    if missing:
        violations.append(f"{etype}: missing fields {sorted(missing)}")
    return violations


# ---------------------------------------------------------------------------
# 1. session_start
# ---------------------------------------------------------------------------


def test_session_start_schema(tmp_path: Path) -> None:
    """session_start event must have all required fields."""
    from scarecrow.session import Session

    session = Session(base_dir=tmp_path)
    session.finalize()

    events = _read_jsonl(session.transcript_path)
    starts = [e for e in events if e["type"] == "session_start"]
    assert len(starts) == 1
    violations = _validate_event(starts[0])
    assert violations == [], f"Schema violations: {violations}"
    # timestamp must be ISO 8601 (contains 'T')
    assert "T" in starts[0]["timestamp"]


# ---------------------------------------------------------------------------
# 2. session_end
# ---------------------------------------------------------------------------


def test_session_end_schema(tmp_path: Path) -> None:
    """session_end event must have all required fields."""
    from scarecrow.session import Session

    session = Session(base_dir=tmp_path)
    session.write_end_header()
    session.finalize()

    events = _read_jsonl(session.transcript_path)
    ends = [e for e in events if e["type"] == "session_end"]
    assert len(ends) == 1
    violations = _validate_event(ends[0])
    assert violations == [], f"Schema violations: {violations}"
    # timestamp must be ISO 8601
    assert "T" in ends[0]["timestamp"]


# ---------------------------------------------------------------------------
# 3. transcript
# ---------------------------------------------------------------------------


async def test_transcript_event_schema(tmp_path: Path) -> None:
    """transcript event emitted by _record_transcript must have all required fields."""
    from scarecrow.app import ScarecrowApp
    from scarecrow.session import Session

    session = Session(base_dir=tmp_path)
    app = ScarecrowApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        app._session = session
        app._elapsed = 42
        app._record_transcript("hello world", batch_elapsed=42, include_ui=False)
    session.finalize()

    events = _read_jsonl(session.transcript_path)
    transcripts = [e for e in events if e["type"] == "transcript"]
    assert len(transcripts) >= 1
    violations = _validate_event(transcripts[0])
    assert violations == [], f"Schema violations: {violations}"
    assert transcripts[0]["text"] == "hello world"
    assert transcripts[0]["elapsed"] == 42


# ---------------------------------------------------------------------------
# 4. note
# ---------------------------------------------------------------------------


async def test_note_event_schema(tmp_path: Path) -> None:
    """note event emitted by _submit_note must have all required fields."""
    from textual.widgets import Input

    from scarecrow.app import ScarecrowApp
    from scarecrow.session import Session

    session = Session(base_dir=tmp_path)
    app = ScarecrowApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        app._session = session
        input_widget = app.query_one("#note-input", Input)
        input_widget.value = "/task do something"
        app._submit_note()
    session.finalize()

    events = _read_jsonl(session.transcript_path)
    notes = [e for e in events if e["type"] == "note"]
    assert len(notes) == 1
    violations = _validate_event(notes[0])
    assert violations == [], f"Schema violations: {violations}"
    assert notes[0]["tag"] == "TASK"
    assert notes[0]["text"] == "do something"


# ---------------------------------------------------------------------------
# 5. warning
# ---------------------------------------------------------------------------


async def test_warning_event_schema(tmp_path: Path) -> None:
    """warning event emitted by _warn_transcript must have all required fields."""
    from scarecrow.app import ScarecrowApp
    from scarecrow.session import Session

    session = Session(base_dir=tmp_path)
    app = ScarecrowApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        app._session = session
        app._warn_transcript("Audio overflow detected")
    session.finalize()

    events = _read_jsonl(session.transcript_path)
    warnings = [e for e in events if e["type"] == "warning"]
    assert len(warnings) == 1
    violations = _validate_event(warnings[0])
    assert violations == [], f"Schema violations: {violations}"
    assert warnings[0]["text"] == "Audio overflow detected"


# ---------------------------------------------------------------------------
# 6. pause
# ---------------------------------------------------------------------------


async def test_pause_event_schema(tmp_path: Path) -> None:
    """pause event emitted by action_pause must have all required fields."""
    from scarecrow.app import AppState, ScarecrowApp
    from scarecrow.session import Session

    session = Session(base_dir=tmp_path)
    mock_rec = MagicMock()

    app = ScarecrowApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        app._session = session
        app._audio_recorder = mock_rec
        app._elapsed = 75
        app.state = AppState.RECORDING
        app.action_pause()
    session.finalize()

    events = _read_jsonl(session.transcript_path)
    pauses = [e for e in events if e["type"] == "pause"]
    assert len(pauses) == 1
    violations = _validate_event(pauses[0])
    assert violations == [], f"Schema violations: {violations}"
    assert pauses[0]["elapsed"] == 75


# ---------------------------------------------------------------------------
# 7. resume
# ---------------------------------------------------------------------------


async def test_resume_event_schema(tmp_path: Path) -> None:
    """resume event (PAUSED->RECORDING toggle) must have all required fields."""
    from scarecrow.app import AppState, ScarecrowApp
    from scarecrow.session import Session

    session = Session(base_dir=tmp_path)
    mock_rec = MagicMock()

    app = ScarecrowApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        app._session = session
        app._audio_recorder = mock_rec
        app._elapsed = 80
        app.state = AppState.PAUSED
        app.action_pause()  # toggles PAUSED → RECORDING, emits resume event
    session.finalize()

    events = _read_jsonl(session.transcript_path)
    resumes = [e for e in events if e["type"] == "resume"]
    assert len(resumes) == 1
    violations = _validate_event(resumes[0])
    assert violations == [], f"Schema violations: {violations}"
    assert resumes[0]["elapsed"] == 80


# ---------------------------------------------------------------------------
# 8. Full-session round-trip: all event types pass schema validation
# ---------------------------------------------------------------------------


def test_all_events_pass_schema_validation(tmp_path: Path) -> None:
    """Every event in a JSONL file written directly must pass schema validation."""
    from scarecrow.session import Session

    session = Session(base_dir=tmp_path)
    session.append_event(
        {
            "type": "transcript",
            "elapsed": 0,
            "timestamp": "2026-03-28T14:00:00",
            "text": "hello",
        }
    )
    session.append_event(
        {"type": "divider", "elapsed": 60, "timestamp": "2026-03-28T14:01:00"}
    )
    session.append_event(
        {
            "type": "note",
            "tag": "TASK",
            "elapsed": 65,
            "timestamp": "2026-03-28T14:01:05",
            "text": "do it",
        }
    )
    session.append_event(
        {
            "type": "warning",
            "elapsed": 70,
            "timestamp": "2026-03-28T14:01:10",
            "text": "overflow",
        }
    )
    session.append_event(
        {"type": "pause", "elapsed": 80, "timestamp": "2026-03-28T14:01:20"}
    )
    session.append_event(
        {"type": "resume", "elapsed": 90, "timestamp": "2026-03-28T14:01:30"}
    )
    session.write_end_header()
    session.finalize()

    events = _read_jsonl(session.transcript_path)
    all_violations: list[str] = []
    for i, event in enumerate(events):
        violations = _validate_event(event)
        if violations:
            all_violations.append(f"Event {i} ({event.get('type', '?')}): {violations}")
    assert all_violations == [], "Schema violations:\n" + "\n".join(all_violations)
