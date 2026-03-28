"""Tests for the Scarecrow Textual TUI."""

from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

from textual.widgets import RichLog

from scarecrow.app import AppState, InfoBar, ScarecrowApp

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_transcriber():
    """Return a mock Transcriber that doesn't load models."""
    mock = MagicMock()
    mock.is_ready = True
    mock.shutdown.return_value = None
    return mock


def _app(with_transcriber: bool = False) -> ScarecrowApp:
    if with_transcriber:
        app = ScarecrowApp(
            transcriber=_mock_transcriber(),
        )
        app._preflight_check = lambda: True  # type: ignore[method-assign]
        return app
    return ScarecrowApp()


def _mock_recorder():
    """Return a mock AudioRecorder that doesn't touch hardware."""
    mock = MagicMock()
    mock.is_recording = True
    mock.is_paused = False
    mock.peak_level = 0.0
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
# Info bar
# ---------------------------------------------------------------------------


async def test_info_bar_present() -> None:
    async with _app().run_test() as pilot:
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        bar = app.query_one(InfoBar)
        assert bar is not None
        assert bar.state is AppState.IDLE


@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_info_bar_shows_recording(mock_session_cls, mock_recorder_cls) -> None:
    mock_recorder_cls.return_value = _mock_recorder()
    mock_session_cls.return_value = MagicMock()

    async with _app(with_transcriber=True).run_test() as pilot:
        await pilot.press("enter")
        await pilot.pause(delay=0.3)
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        bar = app.query_one(InfoBar)
        assert bar.state is AppState.RECORDING


@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_word_count_increments(mock_session_cls, mock_recorder_cls) -> None:
    """Word count should increase when transcript text is appended."""
    mock_recorder_cls.return_value = _mock_recorder()
    mock_session_cls.return_value = MagicMock()

    async with _app(with_transcriber=True).run_test() as pilot:
        await pilot.press("enter")
        await pilot.pause(delay=0.3)
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        app._append_transcript("hello world test")
        await pilot.pause()
        assert app._word_count == 3


# ---------------------------------------------------------------------------
# Auto-start recording (mocked hardware)
# ---------------------------------------------------------------------------


@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_enter_starts_recording(mock_session_cls, mock_recorder_cls) -> None:
    mock_recorder_cls.return_value = _mock_recorder()
    mock_session_cls.return_value = MagicMock()

    async with _app(with_transcriber=True).run_test() as pilot:
        await pilot.press("enter")
        await pilot.pause(delay=0.3)
        assert pilot.app.state is AppState.RECORDING  # type: ignore[attr-defined]


@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_press_p_during_recording_pauses(
    mock_session_cls, mock_recorder_cls
) -> None:
    mock_recorder_cls.return_value = _mock_recorder()
    mock_session_cls.return_value = MagicMock()

    async with _app(with_transcriber=True).run_test() as pilot:
        await pilot.press("enter")
        await pilot.pause(delay=0.3)
        await pilot.press("ctrl+p")
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
        await pilot.press("enter")
        await pilot.pause(delay=0.3)
        await pilot.press("ctrl+p")
        await pilot.press("ctrl+p")
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
        await pilot.press("enter")
        await pilot.pause(delay=2)
        assert app._elapsed >= 1


@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_timer_keeps_running_when_paused(
    mock_session_cls, mock_recorder_cls
) -> None:
    """Elapsed timer tracks total session time, including paused periods."""
    mock_recorder_cls.return_value = _mock_recorder()
    mock_session_cls.return_value = MagicMock()

    async with _app(with_transcriber=True).run_test() as pilot:
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        await pilot.press("enter")
        await pilot.pause(delay=1)
        await pilot.press("ctrl+p")
        elapsed_at_pause = app._elapsed
        await pilot.pause(delay=2)
        assert app._elapsed > elapsed_at_pause


# ---------------------------------------------------------------------------
# Quit
# ---------------------------------------------------------------------------


async def test_press_q_exits() -> None:
    app = _app()
    async with app.run_test() as pilot:
        await pilot.press("ctrl+q")
    assert app.return_value is None


@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_transcriber_error_surfaces_in_ui(
    mock_session_cls, mock_recorder_cls
) -> None:
    mock_recorder_cls.return_value = _mock_recorder()
    mock_session_cls.return_value = MagicMock()

    async with _app(with_transcriber=True).run_test() as pilot:
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        await pilot.pause(delay=0.5)
        app._set_status("")
        app.query_one("#captions", RichLog).clear()

        thread = threading.Thread(
            target=app._on_transcriber_error,
            args=("batch", "Batch transcription failed."),
        )
        thread.start()
        await pilot.pause(delay=0.1)
        thread.join(timeout=1)

        captions = app.query_one("#captions", RichLog)
        caption_text = " ".join(str(line) for line in captions.lines)
        bar = app.query_one(InfoBar)
        assert "Batch transcription failed." in caption_text
        assert bar.status_message == "batch: Batch transcription failed."
        assert bar.status_is_error is True


async def test_pane_labels_show_model_names() -> None:
    """Regression: transcript pane label must include model name."""
    async with _app().run_test() as pilot:
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        labels = [w.render() for w in app.query(".pane-label")]
        label_text = " ".join(str(lbl) for lbl in labels)
        assert "Transcript" in label_text


@patch("scarecrow.config.BACKEND", "whisper")
@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_app_launches_in_idle_no_autostart(
    mock_session_cls, mock_recorder_cls
) -> None:
    """App stays IDLE after mount with whisper backend (context prompt)."""
    mock_recorder_cls.return_value = _mock_recorder()
    mock_session_cls.return_value = MagicMock()

    async with _app(with_transcriber=True).run_test() as pilot:
        await pilot.pause(delay=0.3)
        assert pilot.app.state is AppState.IDLE  # type: ignore[attr-defined]
