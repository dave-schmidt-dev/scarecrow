"""Tests for the Scarecrow Textual TUI."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from scarecrow.app import AppState, ScarecrowApp

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _app() -> ScarecrowApp:
    return ScarecrowApp()


def _mock_recorder():
    """Return a mock AudioRecorder that doesn't touch hardware."""
    mock = MagicMock()
    mock.is_recording = True
    mock.is_paused = False
    mock.start.return_value = None
    mock.stop.return_value = MagicMock()
    return mock


def _mock_transcriber():
    """Return a mock Transcriber that doesn't load models."""
    mock = MagicMock()
    mock.start.return_value = None
    # text() should block forever in real use; in tests we never call it
    mock.text.side_effect = StopIteration
    mock.stop.return_value = None
    mock.shutdown.return_value = None
    return mock


# ---------------------------------------------------------------------------
# Launch
# ---------------------------------------------------------------------------


async def test_app_launches() -> None:
    """App starts without errors."""
    async with _app().run_test() as pilot:
        await pilot.pause()
        assert pilot.app is not None


# ---------------------------------------------------------------------------
# Initial state
# ---------------------------------------------------------------------------


async def test_initial_state_is_idle() -> None:
    async with _app().run_test() as pilot:
        await pilot.pause()
        assert pilot.app.state is AppState.IDLE  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Recording transitions (mocked hardware)
# ---------------------------------------------------------------------------


@patch("scarecrow.app.Transcriber")
@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_press_r_starts_recording(
    mock_session_cls, mock_recorder_cls, mock_transcriber_cls
) -> None:
    mock_recorder_cls.return_value = _mock_recorder()
    mock_transcriber_cls.return_value = _mock_transcriber()
    mock_session_cls.return_value = MagicMock()

    async with _app().run_test() as pilot:
        await pilot.press("r")
        await pilot.pause()
        assert pilot.app.state is AppState.RECORDING  # type: ignore[attr-defined]


@patch("scarecrow.app.Transcriber")
@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_press_p_during_recording_pauses(
    mock_session_cls, mock_recorder_cls, mock_transcriber_cls
) -> None:
    mock_recorder_cls.return_value = _mock_recorder()
    mock_transcriber_cls.return_value = _mock_transcriber()
    mock_session_cls.return_value = MagicMock()

    async with _app().run_test() as pilot:
        await pilot.press("r")
        await pilot.press("p")
        await pilot.pause()
        assert pilot.app.state is AppState.PAUSED  # type: ignore[attr-defined]


@patch("scarecrow.app.Transcriber")
@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_press_p_during_paused_resumes(
    mock_session_cls, mock_recorder_cls, mock_transcriber_cls
) -> None:
    mock_recorder_cls.return_value = _mock_recorder()
    mock_transcriber_cls.return_value = _mock_transcriber()
    mock_session_cls.return_value = MagicMock()

    async with _app().run_test() as pilot:
        await pilot.press("r")
        await pilot.press("p")
        await pilot.press("p")
        await pilot.pause()
        assert pilot.app.state is AppState.RECORDING  # type: ignore[attr-defined]


@patch("scarecrow.app.Transcriber")
@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_press_r_while_recording_is_noop(
    mock_session_cls, mock_recorder_cls, mock_transcriber_cls
) -> None:
    mock_recorder_cls.return_value = _mock_recorder()
    mock_transcriber_cls.return_value = _mock_transcriber()
    mock_session_cls.return_value = MagicMock()

    async with _app().run_test() as pilot:
        await pilot.press("r")
        await pilot.pause()
        assert pilot.app.state is AppState.RECORDING  # type: ignore[attr-defined]
        await pilot.press("r")
        await pilot.pause()
        assert pilot.app.state is AppState.RECORDING  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Elapsed timer (mocked hardware)
# ---------------------------------------------------------------------------


@patch("scarecrow.app.Transcriber")
@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_timer_starts_on_record(
    mock_session_cls, mock_recorder_cls, mock_transcriber_cls
) -> None:
    mock_recorder_cls.return_value = _mock_recorder()
    mock_transcriber_cls.return_value = _mock_transcriber()
    mock_session_cls.return_value = MagicMock()

    async with _app().run_test() as pilot:
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        assert app._elapsed == 0
        await pilot.press("r")
        await pilot.pause(delay=2)
        assert app._elapsed >= 1


@patch("scarecrow.app.Transcriber")
@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_timer_pauses_when_paused(
    mock_session_cls, mock_recorder_cls, mock_transcriber_cls
) -> None:
    mock_recorder_cls.return_value = _mock_recorder()
    mock_transcriber_cls.return_value = _mock_transcriber()
    mock_session_cls.return_value = MagicMock()

    async with _app().run_test() as pilot:
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        await pilot.press("r")
        await pilot.pause(delay=1)
        await pilot.press("p")
        elapsed_at_pause = app._elapsed
        await pilot.pause(delay=2)
        assert app._elapsed == elapsed_at_pause


# ---------------------------------------------------------------------------
# Quit
# ---------------------------------------------------------------------------


async def test_press_q_exits() -> None:
    app = _app()
    async with app.run_test() as pilot:
        await pilot.press("q")
    assert app.return_value is None


# ---------------------------------------------------------------------------
# Public API — update_live_preview / append_caption
# ---------------------------------------------------------------------------


async def test_update_live_preview() -> None:
    from textual.widgets import Static

    async with _app().run_test() as pilot:
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        app.update_live_preview("partial text...")
        await pilot.pause()
        preview = app.query_one("#live-preview", Static)
        assert "partial text..." in str(preview.render())


async def test_append_caption_adds_to_log_and_clears_preview() -> None:
    from textual.widgets import RichLog, Static

    async with _app().run_test() as pilot:
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        app.update_live_preview("in progress...")
        await pilot.pause()
        app.append_caption("Settled sentence.")
        await pilot.pause()
        preview = app.query_one("#live-preview", Static)
        assert str(preview.render()).strip() == ""
        log = app.query_one("#captions", RichLog)
        assert len(log.lines) >= 1
