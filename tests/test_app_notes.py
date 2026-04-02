"""Note submission and note event tests."""

from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import MagicMock, patch

from textual.widgets import Input, RichLog

from scarecrow.app import AppState, ScarecrowApp
from tests.helpers import _app, _mock_recorder, _mock_transcriber, _read_jsonl

# ---------------------------------------------------------------------------
# Phase 5: Note submission logic
# ---------------------------------------------------------------------------


async def test_note_submission_writes_to_richlog() -> None:
    """_submit_note must write the note text to the #captions RichLog."""
    async with _app().run_test() as pilot:
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        await pilot.pause()

        input_widget = app.query_one("#note-input", Input)
        input_widget.value = "test note text"

        captions = app.query_one("#captions", RichLog)
        initial_lines = len(captions.lines)

        app._submit_note()
        await pilot.pause()

        assert len(captions.lines) > initial_lines
        all_text = " ".join(str(line) for line in captions.lines[initial_lines:])
        assert "test note text" in all_text


async def test_note_submission_clears_input() -> None:
    """_submit_note must clear the Input widget value after submission."""
    async with _app().run_test() as pilot:
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        await pilot.pause()

        input_widget = app.query_one("#note-input", Input)
        input_widget.value = "clear me"

        app._submit_note()
        await pilot.pause()

        assert input_widget.value == ""


async def test_note_submission_includes_tag_prefix() -> None:
    """_submit_note must include the tag in the RichLog output."""
    async with _app().run_test() as pilot:
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        await pilot.pause()

        input_widget = app.query_one("#note-input", Input)
        input_widget.value = "/task follow up on this"

        captions = app.query_one("#captions", RichLog)
        initial_lines = len(captions.lines)

        app._submit_note()
        await pilot.pause()

        all_text = " ".join(str(line) for line in captions.lines[initial_lines:])
        assert "TASK" in all_text


async def test_note_submission_increments_word_count() -> None:
    """_submit_note must increment _word_count by the number of words in the note."""
    async with _app().run_test() as pilot:
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        await pilot.pause()

        app._word_count = 0
        input_widget = app.query_one("#note-input", Input)
        input_widget.value = "hello world"

        app._submit_note()
        await pilot.pause()

        assert app._word_count == 2


async def test_empty_note_submission_is_noop() -> None:
    """_submit_note with empty or whitespace input must not write to RichLog."""
    async with _app().run_test() as pilot:
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        # Use a longer delay to ensure any startup messages are already in the
        # RichLog before we snapshot initial_lines.
        await pilot.pause(delay=0.2)

        input_widget = app.query_one("#note-input", Input)
        input_widget.value = "   "

        captions = app.query_one("#captions", RichLog)
        initial_lines = len(captions.lines)
        initial_word_count = app._word_count

        app._submit_note()
        await pilot.pause()

        assert len(captions.lines) == initial_lines
        assert app._word_count == initial_word_count


async def test_note_submission_when_idle() -> None:
    """_submit_note without a session must still write to RichLog (UI-only)."""
    async with _app().run_test() as pilot:
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        await pilot.pause()

        app._session = None
        input_widget = app.query_one("#note-input", Input)
        input_widget.value = "no session note"

        captions = app.query_one("#captions", RichLog)
        initial_lines = len(captions.lines)

        app._submit_note()
        await pilot.pause()

        assert len(captions.lines) > initial_lines
        all_text = " ".join(str(line) for line in captions.lines[initial_lines:])
        assert "no session note" in all_text


async def test_note_submission_writes_to_session(tmp_path: Path) -> None:
    """_submit_note must write the note line to the transcript file via session."""
    from scarecrow.session import Session

    real_session = Session(base_dir=tmp_path)

    async with _app().run_test() as pilot:
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        await pilot.pause()

        app._session = real_session
        input_widget = app.query_one("#note-input", Input)
        input_widget.value = "/task save this note"

        app._submit_note()
        await pilot.pause()

    events = _read_jsonl(real_session.transcript_path)
    note_events = [e for e in events if e.get("type") == "note"]
    assert any(
        e.get("tag") == "TASK" and "save this note" in e.get("text", "")
        for e in note_events
    )


async def test_enter_submits_note() -> None:
    """Pressing Enter in the note Input must submit the note to RichLog."""
    async with _app().run_test() as pilot:
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        await pilot.pause()

        input_widget = app.query_one("#note-input", Input)
        await pilot.click("#note-input")
        await pilot.pause()

        input_widget.value = "enter key note"

        captions = app.query_one("#captions", RichLog)
        initial_lines = len(captions.lines)

        await pilot.press("enter")
        await pilot.pause()

        assert len(captions.lines) > initial_lines
        all_text = " ".join(str(line) for line in captions.lines[initial_lines:])
        assert "enter key note" in all_text


# ---------------------------------------------------------------------------
# Note count tracking
# ---------------------------------------------------------------------------


@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_note_counts_increment(mock_session_cls, mock_recorder_cls) -> None:
    """_note_counts must track counts per type after each submission."""
    mock_recorder_cls.return_value = _mock_recorder()
    mock_session_cls.return_value = MagicMock()

    async with _app(with_transcriber=True).run_test() as pilot:
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        app._start_recording()
        await pilot.pause(delay=0.3)
        assert app.state is AppState.RECORDING

        input_widget = app.query_one("#note-input", Input)

        # Submit a plain note
        input_widget.value = "plain note here"
        app._submit_note()
        await pilot.pause()

        # Submit a /task note
        input_widget.value = "/task follow up on this"
        app._submit_note()
        await pilot.pause()

        assert app._note_counts["NOTE"] == 1
        assert app._note_counts["TASK"] == 1


@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_note_display_shows_task_and_note_counts(
    mock_session_cls, mock_recorder_cls
) -> None:
    """_note_counts must track Tasks and Notes counts after submissions."""
    mock_recorder_cls.return_value = _mock_recorder()
    mock_session_cls.return_value = MagicMock()

    async with _app(with_transcriber=True).run_test() as pilot:
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        app._start_recording()
        await pilot.pause(delay=0.3)
        assert app.state is AppState.RECORDING

        input_widget = app.query_one("#note-input", Input)

        # Add a task note
        input_widget.value = "/task follow up"
        app._submit_note()
        await pilot.pause()

        # Add a plain note
        input_widget.value = "plain note"
        app._submit_note()
        await pilot.pause()

        assert app._note_counts["TASK"] == 1
        assert app._note_counts["NOTE"] == 1


async def test_note_event_has_timestamp_and_elapsed(tmp_path: Path) -> None:
    """note events must include both an ISO 8601 timestamp field and elapsed."""
    from scarecrow.session import Session

    real_session = Session(base_dir=tmp_path)

    async with _app().run_test() as pilot:
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        await pilot.pause()

        app._session = real_session
        app._elapsed = 55
        input_widget = app.query_one("#note-input", Input)
        input_widget.value = "note with timestamp"

        app._submit_note()
        await pilot.pause()

    events = [
        json.loads(line)
        for line in real_session.transcript_path.read_text().splitlines()
    ]
    note_events = [e for e in events if e.get("type") == "note"]
    assert note_events, "Expected at least one note event"
    ev = note_events[0]
    assert "elapsed" in ev, "note event must have 'elapsed' field"
    assert "timestamp" in ev, "note event must have 'timestamp' field"
    pattern = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$"
    assert re.match(pattern, ev["timestamp"]), (
        f"note timestamp {ev['timestamp']!r} does not match ISO 8601"
    )


@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_mn_command_renames_session(mock_session_cls, mock_recorder_cls) -> None:
    """The /mn command must call session.rename() with the provided name."""
    mock_rec = _mock_recorder()
    mock_recorder_cls.return_value = mock_rec
    mock_session = MagicMock()
    mock_session_cls.return_value = mock_session

    app = ScarecrowApp(transcriber=_mock_transcriber())
    app._preflight_check = lambda: True  # type: ignore[method-assign]
    async with app.run_test() as pilot:
        app._start_recording()
        await pilot.pause(delay=0.3)

        input_widget = app.query_one("#note-input", Input)
        input_widget.value = "/mn Standup with Team"
        await pilot.press("enter")
        await pilot.pause()

        mock_session.rename.assert_called_once_with("Standup with Team")
