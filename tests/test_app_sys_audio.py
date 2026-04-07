"""System audio capture lifecycle, echo filter, quit paths, and launch flag tests."""

from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

from textual.widgets import RichLog

from scarecrow.app import AppState, InfoBar, ScarecrowApp
from tests.helpers import _mock_recorder, _mock_sys_capture, _mock_transcriber, _sys_app

# ---------------------------------------------------------------------------
# _start_recording() with sys audio
# ---------------------------------------------------------------------------


@patch("scarecrow.sys_audio.SystemAudioCapture")
@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_start_recording_with_blackhole_starts_sys_capture(
    mock_session, mock_rec, mock_sac
) -> None:
    """When BlackHole is found, SystemAudioCapture.start() is called."""
    mock_sys = _mock_sys_capture()
    mock_sac.return_value = mock_sys
    mock_rec.return_value = _mock_recorder()
    mock_session.return_value = MagicMock()

    async with _sys_app().run_test() as pilot:
        await pilot.press("enter")
        await pilot.pause(delay=0.3)
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        assert app.state is AppState.RECORDING
        mock_sys.start.assert_called_once()
        assert app._sys_capture is mock_sys


@patch("scarecrow.sys_audio.SystemAudioCapture")
@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_start_recording_without_tap_no_sys_capture(
    mock_session, mock_rec, mock_sac
) -> None:
    """When no tap handle is provided, _sys_capture stays None."""
    mock_rec.return_value = _mock_recorder()
    mock_session.return_value = MagicMock()

    async with _sys_app(tap_handle=None).run_test() as pilot:
        await pilot.press("enter")
        await pilot.pause(delay=0.3)
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        assert app.state is AppState.RECORDING
        assert app._sys_capture is None
        mock_sac.assert_not_called()


@patch("scarecrow.sys_audio.SystemAudioCapture")
@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_start_recording_sys_capture_exception_mic_continues(
    mock_session, mock_rec, mock_sac
) -> None:
    """If sys capture .start() raises, app stays RECORDING with _sys_capture=None."""
    mock_sys = _mock_sys_capture()
    mock_sys.start.side_effect = OSError("device busy")
    mock_sac.return_value = mock_sys
    mock_rec.return_value = _mock_recorder()
    mock_session.return_value = MagicMock()

    async with _sys_app().run_test() as pilot:
        await pilot.press("enter")
        await pilot.pause(delay=0.3)
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        assert app.state is AppState.RECORDING
        assert app._sys_capture is None


@patch("scarecrow.sys_audio.SystemAudioCapture")
@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_info_bar_has_sys_audio_when_capture_present(
    mock_session, mock_rec, mock_sac
) -> None:
    """InfoBar.has_sys_audio is True when sys capture is active."""
    mock_sys = _mock_sys_capture()
    mock_sac.return_value = mock_sys
    mock_rec.return_value = _mock_recorder()
    mock_session.return_value = MagicMock()

    async with _sys_app().run_test() as pilot:
        await pilot.press("enter")
        await pilot.pause(delay=0.3)
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        bar = app.query_one(InfoBar)
        assert bar.has_sys_audio is True


@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_info_bar_no_sys_audio_without_capture(mock_session, mock_rec) -> None:
    """InfoBar.has_sys_audio is False when sys_audio=False."""
    mock_rec.return_value = _mock_recorder()
    mock_session.return_value = MagicMock()

    app = ScarecrowApp(transcriber=_mock_transcriber(), sys_audio=False)
    app._preflight_check = lambda: True  # type: ignore[method-assign]
    async with app.run_test() as pilot:
        await pilot.press("enter")
        await pilot.pause(delay=0.3)
        bar = app.query_one(InfoBar)
        assert bar.has_sys_audio is False


# ---------------------------------------------------------------------------
# _on_sys_batch_result() and echo filter
# ---------------------------------------------------------------------------


@patch("scarecrow.sys_audio.SystemAudioCapture")
@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_sys_batch_result_records_to_echo_filter(
    mock_session, mock_rec, mock_sac
) -> None:
    """_on_sys_batch_result() registers the text in the echo filter."""
    mock_sys = _mock_sys_capture()
    mock_sac.return_value = mock_sys
    mock_rec.return_value = _mock_recorder()
    mock_session.return_value = MagicMock()

    async with _sys_app().run_test() as pilot:
        await pilot.press("enter")
        await pilot.pause(delay=0.3)
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]

        thread = threading.Thread(
            target=app._on_sys_batch_result,
            args=("hello world test", 10),
        )
        thread.start()
        await pilot.pause(delay=0.1)
        thread.join(timeout=1)

        # Echo filter should now have an entry for "hello world test"
        assert app._echo_filter.is_echo("hello world test") is True


@patch("scarecrow.sys_audio.SystemAudioCapture")
@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_sys_batch_result_writes_to_richlog(
    mock_session, mock_rec, mock_sac
) -> None:
    """_on_sys_batch_result() writes content to the RichLog captions widget."""
    mock_sys = _mock_sys_capture()
    mock_sac.return_value = mock_sys
    mock_rec.return_value = _mock_recorder()
    mock_session.return_value = MagicMock()

    async with _sys_app().run_test() as pilot:
        await pilot.press("enter")
        await pilot.pause(delay=0.3)
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]

        captions = app.query_one("#captions", RichLog)
        captions.clear()

        thread = threading.Thread(
            target=app._on_sys_batch_result,
            args=("system audio transcript content", 5),
        )
        thread.start()
        await pilot.pause(delay=0.2)
        thread.join(timeout=1)

        caption_text = " ".join(str(line) for line in captions.lines)
        assert "system audio transcript content" in caption_text


@patch("scarecrow.sys_audio.SystemAudioCapture")
@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_echo_filter_suppresses_duplicate(
    mock_session, mock_rec, mock_sac
) -> None:
    """Mic result is suppressed by echo filter when sys already recorded same text."""
    mock_sys = _mock_sys_capture()
    mock_sac.return_value = mock_sys
    mock_rec.return_value = _mock_recorder()
    mock_session.return_value = MagicMock()

    async with _sys_app().run_test() as pilot:
        await pilot.press("enter")
        await pilot.pause(delay=0.3)
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]

        captions = app.query_one("#captions", RichLog)
        captions.clear()

        # Record the sys transcript first
        sys_thread = threading.Thread(
            target=app._on_sys_batch_result,
            args=("remote speaker says hello world today", 5),
        )
        sys_thread.start()
        await pilot.pause(delay=0.1)
        sys_thread.join(timeout=1)

        line_count_after_sys = len(captions.lines)

        # Now send same text via mic — should be suppressed
        mic_thread = threading.Thread(
            target=app._on_batch_result,
            args=("remote speaker says hello world today", 5),
        )
        mic_thread.start()
        await pilot.pause(delay=0.1)
        mic_thread.join(timeout=1)

        # Line count should not have grown from the suppressed mic result
        assert len(captions.lines) == line_count_after_sys


# ---------------------------------------------------------------------------
# Shutdown with sys audio
# ---------------------------------------------------------------------------


@patch("scarecrow.audio_tap.destroy_system_tap")
@patch("scarecrow.sys_audio.SystemAudioCapture")
@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_cleanup_stops_sys_capture(
    mock_session, mock_rec, mock_sac, mock_destroy_tap
) -> None:
    """cleanup_after_exit() calls stop() on the sys capture."""
    mock_sys = _mock_sys_capture()
    mock_sac.return_value = mock_sys
    mock_rec.return_value = _mock_recorder()
    mock_session.return_value = MagicMock()

    async with _sys_app().run_test() as pilot:
        await pilot.press("enter")
        await pilot.pause(delay=0.3)
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        assert app._sys_capture is mock_sys
        mock_sys.stop.reset_mock()
        app.cleanup_after_exit(include_ui=True)
        await pilot.pause(delay=0.1)
        mock_sys.stop.assert_called()


@patch("scarecrow.audio_tap.destroy_system_tap")
@patch("scarecrow.sys_audio.SystemAudioCapture")
@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_cleanup_clears_sys_capture_ref(
    mock_session, mock_rec, mock_sac, mock_destroy_tap
) -> None:
    """After cleanup_after_exit(), app._sys_capture is None."""
    mock_sys = _mock_sys_capture()
    mock_sac.return_value = mock_sys
    mock_rec.return_value = _mock_recorder()
    mock_session.return_value = MagicMock()

    async with _sys_app().run_test() as pilot:
        await pilot.press("enter")
        await pilot.pause(delay=0.3)
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        app.cleanup_after_exit(include_ui=True)
        await pilot.pause(delay=0.1)
        assert app._sys_capture is None


@patch("scarecrow.sys_audio.SystemAudioCapture")
@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_post_exit_cleanup_compresses_sys_audio(
    mock_session, mock_rec, mock_sac
) -> None:
    """post_exit_cleanup() calls compress_sys_audio when sys_audio is enabled."""
    mock_sys = _mock_sys_capture()
    mock_sac.return_value = mock_sys
    mock_rec.return_value = _mock_recorder()
    mock_session = MagicMock()
    mock_session.return_value = mock_session

    async with _sys_app().run_test() as pilot:
        await pilot.press("enter")
        await pilot.pause(delay=0.3)
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        # Simulate completed session reference (normally set by cleanup_after_exit)
        app._completed_session = mock_session
        app._sys_audio_enabled = True
        app._skip_summary = True  # avoid hitting summarizer
        app.post_exit_cleanup()
        mock_session.compress_sys_audio_segment.assert_called_once_with(1)


@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_post_exit_cleanup_skips_when_disabled(mock_session, mock_rec) -> None:
    """post_exit_cleanup() does not call compress_sys_audio when sys_audio=False."""
    mock_rec.return_value = _mock_recorder()
    mock_session = MagicMock()
    mock_session.return_value = mock_session

    app = ScarecrowApp(transcriber=_mock_transcriber(), sys_audio=False)
    app._preflight_check = lambda: True  # type: ignore[method-assign]
    async with app.run_test() as pilot:
        await pilot.press("enter")
        await pilot.pause(delay=0.3)
        app._completed_session = mock_session
        app._sys_audio_enabled = False
        app._skip_summary = True
        app.post_exit_cleanup()
        mock_session.compress_sys_audio_segment.assert_not_called()


# ---------------------------------------------------------------------------
# Footer binding visibility — Quick Quit hidden (Bug 3)
# ---------------------------------------------------------------------------


async def test_quick_quit_binding_is_shown_in_footer() -> None:
    """Quick Quit binding must have show=True so it appears in the footer."""
    from scarecrow.app import ScarecrowApp

    bindings = [b for b in ScarecrowApp.BINDINGS if b.action == "quick_quit"]
    assert bindings, "quick_quit binding not found in BINDINGS"
    assert bindings[0].show, "quick_quit binding should have show=True"


@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_quick_quit_action_sets_skip_summary(mock_session, mock_rec) -> None:
    """action_quick_quit() sets _skip_summary=True when invoked."""
    recorder = _mock_recorder()
    mock_rec.return_value = recorder
    mock_session.return_value = MagicMock()

    session_mock = MagicMock()
    session_mock.audio_path.exists.return_value = False
    session_mock.transcript_path.exists.return_value = False
    mock_session.return_value = session_mock

    app = ScarecrowApp(transcriber=_mock_transcriber(), sys_audio=False)
    app._preflight_check = lambda: True  # type: ignore[method-assign]
    async with app.run_test() as pilot:
        await pilot.press("enter")
        await pilot.pause(delay=0.3)
        assert app.state is AppState.RECORDING
        assert app._skip_summary is False
        # Patch _deferred_quit so the app doesn't actually exit during the test
        app._deferred_quit = lambda: None  # type: ignore[method-assign]
        app.action_quick_quit()
        assert app._skip_summary is True


# ---------------------------------------------------------------------------
# _check_device_loss() skips restart when mic is muted (Bug 1)
# ---------------------------------------------------------------------------


@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_check_device_loss_skips_restart_when_mic_muted(
    mock_session, mock_rec
) -> None:
    """_check_device_loss() must not call restart_stream() when mic is muted."""
    recorder = _mock_recorder()
    mock_rec.return_value = recorder
    mock_session.return_value = MagicMock()

    app = ScarecrowApp(transcriber=_mock_transcriber(), sys_audio=False)
    app._preflight_check = lambda: True  # type: ignore[method-assign]
    async with app.run_test() as pilot:
        await pilot.press("enter")
        await pilot.pause(delay=0.3)
        assert app.state is AppState.RECORDING
        # Mute the mic, then simulate a stale callback window
        app._mic_muted = True
        recorder.seconds_since_last_callback = 5.0  # > _DEVICE_LOSS_THRESHOLD (3.0)
        recorder.default_device_changed = False
        recorder.restart_stream.reset_mock()
        app._check_device_loss()
        recorder.restart_stream.assert_not_called()


@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_check_device_loss_restarts_when_mic_not_muted(
    mock_session, mock_rec
) -> None:
    """restart_stream() fires when threshold exceeded and mic not muted."""
    recorder = _mock_recorder()
    mock_rec.return_value = recorder
    mock_session.return_value = MagicMock()

    app = ScarecrowApp(transcriber=_mock_transcriber(), sys_audio=False)
    app._preflight_check = lambda: True  # type: ignore[method-assign]
    async with app.run_test() as pilot:
        await pilot.press("enter")
        await pilot.pause(delay=0.3)
        assert app.state is AppState.RECORDING
        assert app._mic_muted is False
        recorder.seconds_since_last_callback = 5.0  # > _DEVICE_LOSS_THRESHOLD (3.0)
        recorder.default_device_changed = False
        recorder.restart_stream.reset_mock()
        app._check_device_loss()
        recorder.restart_stream.assert_called_once()


# ---------------------------------------------------------------------------
# Launch flags: --mic-only / --sys-only (initial mute state)
# ---------------------------------------------------------------------------


@patch("scarecrow.sys_audio.SystemAudioCapture")
@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_mic_only_flag_starts_sys_muted(mock_session, mock_rec, mock_sac) -> None:
    """--mic-only flag starts with sys audio muted and paused."""
    mock_sys = _mock_sys_capture()
    mock_sac.return_value = mock_sys
    mock_rec.return_value = _mock_recorder()
    mock_session.return_value = MagicMock()

    async with _sys_app(sys_muted=True).run_test() as pilot:
        await pilot.press("enter")
        await pilot.pause(delay=0.3)
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        assert app.state is AppState.RECORDING
        assert app._sys_muted is True
        assert app._mic_muted is False
        mock_sys.pause.assert_called_once()


@patch("scarecrow.sys_audio.SystemAudioCapture")
@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_sys_only_flag_starts_mic_muted(mock_session, mock_rec, mock_sac) -> None:
    """--sys-only flag starts with mic muted and paused."""
    mock_sys = _mock_sys_capture()
    mock_sac.return_value = mock_sys
    mock_recorder = _mock_recorder()
    mock_rec.return_value = mock_recorder
    mock_session.return_value = MagicMock()

    async with _sys_app(mic_muted=True).run_test() as pilot:
        await pilot.press("enter")
        await pilot.pause(delay=0.3)
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        assert app.state is AppState.RECORDING
        assert app._mic_muted is True
        assert app._sys_muted is False
        mock_recorder.pause.assert_called_once()
