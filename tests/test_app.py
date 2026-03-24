"""Tests for the Scarecrow Textual TUI."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from textual.widgets import RichLog

from scarecrow.app import AppState, ScarecrowApp

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_transcriber():
    """Return a mock Transcriber that doesn't load models."""
    mock = MagicMock()
    mock.is_ready = True
    mock.text.side_effect = StopIteration
    mock.set_callbacks.return_value = None
    mock.shutdown.return_value = None
    return mock


def _app(with_transcriber: bool = False) -> ScarecrowApp:
    if with_transcriber:
        return ScarecrowApp(transcriber=_mock_transcriber())
    return ScarecrowApp()


def _mock_recorder():
    """Return a mock AudioRecorder that doesn't touch hardware."""
    mock = MagicMock()
    mock.is_recording = True
    mock.is_paused = False
    mock.start.return_value = None
    mock.stop.return_value = MagicMock()
    return mock


# ---------------------------------------------------------------------------
# Launch
# ---------------------------------------------------------------------------


async def test_app_launches() -> None:
    async with _app().run_test() as pilot:
        await pilot.pause()
        assert pilot.app is not None


# ---------------------------------------------------------------------------
# Initial state (no transcriber = stays idle)
# ---------------------------------------------------------------------------


async def test_initial_state_without_transcriber_is_idle() -> None:
    async with _app().run_test() as pilot:
        await pilot.pause()
        assert pilot.app.state is AppState.IDLE  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Auto-start recording (mocked hardware)
# ---------------------------------------------------------------------------


@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_auto_starts_recording(mock_session_cls, mock_recorder_cls) -> None:
    mock_recorder_cls.return_value = _mock_recorder()
    mock_session_cls.return_value = MagicMock()

    async with _app(with_transcriber=True).run_test() as pilot:
        await pilot.pause(delay=0.5)
        assert pilot.app.state is AppState.RECORDING  # type: ignore[attr-defined]


@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_press_p_during_recording_pauses(
    mock_session_cls, mock_recorder_cls
) -> None:
    mock_recorder_cls.return_value = _mock_recorder()
    mock_session_cls.return_value = MagicMock()

    async with _app(with_transcriber=True).run_test() as pilot:
        await pilot.pause(delay=0.5)
        await pilot.press("p")
        await pilot.pause()
        assert pilot.app.state is AppState.PAUSED  # type: ignore[attr-defined]


@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_press_p_during_paused_resumes(
    mock_session_cls, mock_recorder_cls
) -> None:
    mock_recorder_cls.return_value = _mock_recorder()
    mock_session_cls.return_value = MagicMock()

    async with _app(with_transcriber=True).run_test() as pilot:
        await pilot.pause(delay=0.5)
        await pilot.press("p")
        await pilot.press("p")
        await pilot.pause()
        assert pilot.app.state is AppState.RECORDING  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Elapsed timer (mocked hardware)
# ---------------------------------------------------------------------------


@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_timer_starts_on_auto_record(mock_session_cls, mock_recorder_cls) -> None:
    mock_recorder_cls.return_value = _mock_recorder()
    mock_session_cls.return_value = MagicMock()

    async with _app(with_transcriber=True).run_test() as pilot:
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        await pilot.pause(delay=2)
        assert app._elapsed >= 1


@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_timer_pauses_when_paused(mock_session_cls, mock_recorder_cls) -> None:
    mock_recorder_cls.return_value = _mock_recorder()
    mock_session_cls.return_value = MagicMock()

    async with _app(with_transcriber=True).run_test() as pilot:
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
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
    async with _app().run_test() as pilot:
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        app.update_live_preview("partial text...")
        await pilot.pause()
        live_log = app.query_one("#live-log", RichLog)
        assert len(live_log.lines) >= 1


async def test_append_caption_adds_to_log_and_clears_live() -> None:
    async with _app().run_test() as pilot:
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        app.update_live_preview("in progress...")
        await pilot.pause()
        app.append_caption("Settled sentence.")
        await pilot.pause()
        captions = app.query_one("#captions", RichLog)
        assert len(captions.lines) >= 1
        live_log = app.query_one("#live-log", RichLog)
        assert len(live_log.lines) == 0
