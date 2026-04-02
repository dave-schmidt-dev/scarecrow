"""Integration tests for sys-audio branches in app.py.

Covers all sys-audio code paths that have zero coverage from other test files:
_start_recording() with sys audio, action_mute_sys(), action_mute_mic(),
_sys_vad_transcribe(), _on_sys_batch_result(), echo filter, and shutdown.
"""

from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

import numpy as np
from textual.widgets import OptionList, RichLog

from scarecrow.app import AppState, ContextMenuScreen, InfoBar, ScarecrowApp

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_transcriber():
    mock = MagicMock()
    mock.is_ready = True
    mock.consecutive_failures = 0
    mock.shutdown.return_value = None

    def _shutdown(timeout=5):
        mock.is_ready = False

    mock.shutdown.side_effect = _shutdown
    return mock


def _mock_recorder():
    mock = MagicMock()
    mock.is_recording = True
    mock.is_paused = False
    mock.peak_level = 0.0
    mock.seconds_since_last_callback = 0.0
    mock.buffer_seconds = 0.0
    mock._last_warning = None
    mock._disk_write_failed = False
    mock.default_device_changed = False
    mock.start.return_value = None
    mock.stop.return_value = MagicMock()
    mock.drain_to_silence.return_value = None
    mock.drain_buffer.return_value = None
    return mock


def _mock_sys_capture():
    mock = MagicMock()
    mock.is_recording = True
    mock.is_paused = False
    mock.peak_level = 0.0
    mock.buffer_seconds = 0.0
    mock.start.return_value = None
    mock.stop.return_value = None
    mock.pause.return_value = None
    mock.resume.return_value = None
    mock.drain_to_silence.return_value = None
    mock.drain_buffer.return_value = None
    return mock


def _sys_app(**kwargs) -> ScarecrowApp:
    """Return a ScarecrowApp with sys_audio=True and a ready transcriber."""
    app = ScarecrowApp(transcriber=_mock_transcriber(), sys_audio=True, **kwargs)
    app._preflight_check = lambda: True  # type: ignore[method-assign]
    return app


# ---------------------------------------------------------------------------
# _start_recording() with sys audio
# ---------------------------------------------------------------------------


@patch("scarecrow.sys_audio.find_blackhole_device", return_value=3)
@patch("scarecrow.sys_audio.SystemAudioCapture")
@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_start_recording_with_blackhole_starts_sys_capture(
    mock_session, mock_rec, mock_sac, mock_bh
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


@patch("scarecrow.sys_audio.find_blackhole_device", return_value=None)
@patch("scarecrow.sys_audio.SystemAudioCapture")
@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_start_recording_without_blackhole_no_sys_capture(
    mock_session, mock_rec, mock_sac, mock_bh
) -> None:
    """When no BlackHole device is found, _sys_capture stays None."""
    mock_rec.return_value = _mock_recorder()
    mock_session.return_value = MagicMock()

    async with _sys_app().run_test() as pilot:
        await pilot.press("enter")
        await pilot.pause(delay=0.3)
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        assert app.state is AppState.RECORDING
        assert app._sys_capture is None
        mock_sac.assert_not_called()


@patch("scarecrow.sys_audio.find_blackhole_device", return_value=3)
@patch("scarecrow.sys_audio.SystemAudioCapture")
@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_start_recording_sys_capture_exception_mic_continues(
    mock_session, mock_rec, mock_sac, mock_bh
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


@patch("scarecrow.sys_audio.find_blackhole_device", return_value=3)
@patch("scarecrow.sys_audio.SystemAudioCapture")
@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_info_bar_has_sys_audio_when_capture_present(
    mock_session, mock_rec, mock_sac, mock_bh
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
# action_mute_sys()
# ---------------------------------------------------------------------------


@patch("scarecrow.sys_audio.find_blackhole_device", return_value=3)
@patch("scarecrow.sys_audio.SystemAudioCapture")
@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_mute_sys_toggles_flag(mock_session, mock_rec, mock_sac, mock_bh) -> None:
    """action_mute_sys() sets _sys_muted to True on first call."""
    mock_sys = _mock_sys_capture()
    mock_sac.return_value = mock_sys
    mock_rec.return_value = _mock_recorder()
    mock_session.return_value = MagicMock()

    async with _sys_app().run_test() as pilot:
        await pilot.press("enter")
        await pilot.pause(delay=0.3)
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        assert app._sys_muted is False
        app.action_mute_sys()
        assert app._sys_muted is True


@patch("scarecrow.sys_audio.find_blackhole_device", return_value=3)
@patch("scarecrow.sys_audio.SystemAudioCapture")
@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_mute_sys_pauses_capture(
    mock_session, mock_rec, mock_sac, mock_bh
) -> None:
    """action_mute_sys() calls pause() on the sys capture."""
    mock_sys = _mock_sys_capture()
    mock_sac.return_value = mock_sys
    mock_rec.return_value = _mock_recorder()
    mock_session.return_value = MagicMock()

    async with _sys_app().run_test() as pilot:
        await pilot.press("enter")
        await pilot.pause(delay=0.3)
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        mock_sys.pause.reset_mock()
        app.action_mute_sys()
        mock_sys.pause.assert_called_once()


@patch("scarecrow.sys_audio.find_blackhole_device", return_value=3)
@patch("scarecrow.sys_audio.SystemAudioCapture")
@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_unmute_sys_resumes_capture(
    mock_session, mock_rec, mock_sac, mock_bh
) -> None:
    """action_mute_sys() twice: second call resumes the capture."""
    mock_sys = _mock_sys_capture()
    mock_sac.return_value = mock_sys
    mock_rec.return_value = _mock_recorder()
    mock_session.return_value = MagicMock()

    async with _sys_app().run_test() as pilot:
        await pilot.press("enter")
        await pilot.pause(delay=0.3)
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        app.action_mute_sys()  # mute
        mock_sys.resume.reset_mock()
        app.action_mute_sys()  # unmute
        mock_sys.resume.assert_called_once()
        assert app._sys_muted is False


@patch("scarecrow.sys_audio.find_blackhole_device", return_value=3)
@patch("scarecrow.sys_audio.SystemAudioCapture")
@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_mute_sys_noop_when_not_recording(
    mock_session, mock_rec, mock_sac, mock_bh
) -> None:
    """action_mute_sys() does nothing when state is not RECORDING."""
    mock_sys = _mock_sys_capture()
    mock_sac.return_value = mock_sys
    mock_rec.return_value = _mock_recorder()
    mock_session.return_value = MagicMock()

    async with _sys_app().run_test() as pilot:
        await pilot.press("enter")
        await pilot.pause(delay=0.3)
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        # Force state to IDLE without going through normal shutdown
        app._reactive_state = AppState.IDLE
        app.action_mute_sys()
        assert app._sys_muted is False
        mock_sys.pause.assert_not_called()


@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_mute_sys_noop_when_no_capture(mock_session, mock_rec) -> None:
    """action_mute_sys() with no sys capture does not crash."""
    mock_rec.return_value = _mock_recorder()
    mock_session.return_value = MagicMock()

    app = ScarecrowApp(transcriber=_mock_transcriber(), sys_audio=False)
    app._preflight_check = lambda: True  # type: ignore[method-assign]
    async with app.run_test() as pilot:
        await pilot.press("enter")
        await pilot.pause(delay=0.3)
        assert app.state is AppState.RECORDING
        assert app._sys_capture is None
        # Must not raise
        app.action_mute_sys()
        assert app._sys_muted is False


@patch("scarecrow.sys_audio.find_blackhole_device", return_value=3)
@patch("scarecrow.sys_audio.SystemAudioCapture")
@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_info_bar_reflects_sys_muted(
    mock_session, mock_rec, mock_sac, mock_bh
) -> None:
    """After action_mute_sys(), InfoBar.sys_muted is True."""
    mock_sys = _mock_sys_capture()
    mock_sac.return_value = mock_sys
    mock_rec.return_value = _mock_recorder()
    mock_session.return_value = MagicMock()

    async with _sys_app().run_test() as pilot:
        await pilot.press("enter")
        await pilot.pause(delay=0.3)
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        app.action_mute_sys()
        await pilot.pause()
        bar = app.query_one(InfoBar)
        assert bar.sys_muted is True


# ---------------------------------------------------------------------------
# action_mute_mic()
# ---------------------------------------------------------------------------


@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_mute_mic_toggles_flag(mock_session, mock_rec) -> None:
    """action_mute_mic() sets _mic_muted to True on first call."""
    mock_rec.return_value = _mock_recorder()
    mock_session.return_value = MagicMock()

    app = ScarecrowApp(transcriber=_mock_transcriber(), sys_audio=False)
    app._preflight_check = lambda: True  # type: ignore[method-assign]
    async with app.run_test() as pilot:
        await pilot.press("enter")
        await pilot.pause(delay=0.3)
        assert app.state is AppState.RECORDING
        assert app._mic_muted is False
        app.action_mute_mic()
        assert app._mic_muted is True


@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_mute_mic_pauses_recorder(mock_session, mock_rec) -> None:
    """action_mute_mic() calls pause() on the audio recorder."""
    recorder = _mock_recorder()
    mock_rec.return_value = recorder
    mock_session.return_value = MagicMock()

    app = ScarecrowApp(transcriber=_mock_transcriber(), sys_audio=False)
    app._preflight_check = lambda: True  # type: ignore[method-assign]
    async with app.run_test() as pilot:
        await pilot.press("enter")
        await pilot.pause(delay=0.3)
        recorder.pause.reset_mock()
        app.action_mute_mic()
        recorder.pause.assert_called_once()


@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_unmute_mic_resumes_recorder(mock_session, mock_rec) -> None:
    """action_mute_mic() twice: second call resumes the recorder."""
    recorder = _mock_recorder()
    mock_rec.return_value = recorder
    mock_session.return_value = MagicMock()

    app = ScarecrowApp(transcriber=_mock_transcriber(), sys_audio=False)
    app._preflight_check = lambda: True  # type: ignore[method-assign]
    async with app.run_test() as pilot:
        await pilot.press("enter")
        await pilot.pause(delay=0.3)
        app.action_mute_mic()  # mute
        recorder.resume.reset_mock()
        app.action_mute_mic()  # unmute
        recorder.resume.assert_called_once()
        assert app._mic_muted is False


@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_info_bar_reflects_mic_muted(mock_session, mock_rec) -> None:
    """After action_mute_mic(), InfoBar.mic_muted is True."""
    recorder = _mock_recorder()
    mock_rec.return_value = recorder
    mock_session.return_value = MagicMock()

    app = ScarecrowApp(transcriber=_mock_transcriber(), sys_audio=False)
    app._preflight_check = lambda: True  # type: ignore[method-assign]
    async with app.run_test() as pilot:
        await pilot.press("enter")
        await pilot.pause(delay=0.3)
        app.action_mute_mic()
        await pilot.pause()
        bar = app.query_one(InfoBar)
        assert bar.mic_muted is True


# ---------------------------------------------------------------------------
# _sys_vad_transcribe()
# ---------------------------------------------------------------------------


@patch("scarecrow.sys_audio.find_blackhole_device", return_value=3)
@patch("scarecrow.sys_audio.SystemAudioCapture")
@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_sys_vad_calls_drain_to_silence(
    mock_session, mock_rec, mock_sac, mock_bh
) -> None:
    """_sys_vad_transcribe() calls drain_to_silence on the sys capture."""
    mock_sys = _mock_sys_capture()
    mock_sys.drain_to_silence.return_value = None
    mock_sac.return_value = mock_sys
    mock_rec.return_value = _mock_recorder()
    mock_session.return_value = MagicMock()

    async with _sys_app().run_test() as pilot:
        await pilot.press("enter")
        await pilot.pause(delay=0.3)
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        mock_sys.drain_to_silence.reset_mock()
        app._sys_vad_transcribe()
        mock_sys.drain_to_silence.assert_called_once()


@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_sys_vad_skips_when_no_sys_capture(mock_session, mock_rec) -> None:
    """_sys_vad_transcribe() does not crash when _sys_capture is None."""
    mock_rec.return_value = _mock_recorder()
    mock_session.return_value = MagicMock()

    app = ScarecrowApp(transcriber=_mock_transcriber(), sys_audio=False)
    app._preflight_check = lambda: True  # type: ignore[method-assign]
    async with app.run_test() as pilot:
        await pilot.press("enter")
        await pilot.pause(delay=0.3)
        assert app._sys_capture is None
        # Must not raise
        app._sys_vad_transcribe()


@patch("scarecrow.sys_audio.find_blackhole_device", return_value=3)
@patch("scarecrow.sys_audio.SystemAudioCapture")
@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_sys_vad_submits_batch_when_audio_ready(
    mock_session, mock_rec, mock_sac, mock_bh
) -> None:
    """_sys_vad_transcribe() submits a batch future when drain returns audio."""
    mock_sys = _mock_sys_capture()
    # Return audio with energies above the speech ratio threshold
    audio = np.zeros(16000, dtype="float32")
    # All high energies so speech_ratio check passes
    energies = [0.5] * 20
    mock_sys.drain_to_silence.return_value = (audio, energies)
    mock_sac.return_value = mock_sys
    mock_rec.return_value = _mock_recorder()
    mock_session.return_value = MagicMock()

    async with _sys_app().run_test() as pilot:
        await pilot.press("enter")
        await pilot.pause(delay=0.3)
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        # Clear any batch futures from prior VAD polls
        app._batch_futures.clear()
        app._sys_vad_transcribe()
        assert len(app._batch_futures) > 0


@patch("scarecrow.sys_audio.find_blackhole_device", return_value=3)
@patch("scarecrow.sys_audio.SystemAudioCapture")
@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_on_vad_poll_runs_sys_vad_when_not_muted(
    mock_session, mock_rec, mock_sac, mock_bh
) -> None:
    """_on_vad_poll() calls drain_to_silence when sys capture present and unmuted."""
    mock_sys = _mock_sys_capture()
    mock_sys.drain_to_silence.return_value = None
    mock_sac.return_value = mock_sys
    mock_rec.return_value = _mock_recorder()
    mock_session.return_value = MagicMock()

    async with _sys_app().run_test() as pilot:
        await pilot.press("enter")
        await pilot.pause(delay=0.3)
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        assert app._sys_muted is False
        mock_sys.drain_to_silence.reset_mock()
        app._on_vad_poll()
        mock_sys.drain_to_silence.assert_called()


@patch("scarecrow.sys_audio.find_blackhole_device", return_value=3)
@patch("scarecrow.sys_audio.SystemAudioCapture")
@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_on_vad_poll_skips_sys_vad_when_muted(
    mock_session, mock_rec, mock_sac, mock_bh
) -> None:
    """_on_vad_poll() does not call drain_to_silence when sys is muted."""
    mock_sys = _mock_sys_capture()
    mock_sys.drain_to_silence.return_value = None
    mock_sac.return_value = mock_sys
    mock_rec.return_value = _mock_recorder()
    mock_session.return_value = MagicMock()

    async with _sys_app().run_test() as pilot:
        await pilot.press("enter")
        await pilot.pause(delay=0.3)
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        app._sys_muted = True
        mock_sys.drain_to_silence.reset_mock()
        app._on_vad_poll()
        mock_sys.drain_to_silence.assert_not_called()


# ---------------------------------------------------------------------------
# _on_sys_batch_result() and echo filter
# ---------------------------------------------------------------------------


@patch("scarecrow.sys_audio.find_blackhole_device", return_value=3)
@patch("scarecrow.sys_audio.SystemAudioCapture")
@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_sys_batch_result_records_to_echo_filter(
    mock_session, mock_rec, mock_sac, mock_bh
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


@patch("scarecrow.sys_audio.find_blackhole_device", return_value=3)
@patch("scarecrow.sys_audio.SystemAudioCapture")
@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_sys_batch_result_writes_to_richlog(
    mock_session, mock_rec, mock_sac, mock_bh
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

        # Clear the sys holdoff (first result after start is discarded)
        app._on_sys_batch_result("holdoff primer", 1)

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


@patch("scarecrow.sys_audio.find_blackhole_device", return_value=3)
@patch("scarecrow.sys_audio.SystemAudioCapture")
@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_echo_filter_suppresses_duplicate(
    mock_session, mock_rec, mock_sac, mock_bh
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


@patch("scarecrow.sys_audio.find_blackhole_device", return_value=3)
@patch("scarecrow.sys_audio.SystemAudioCapture")
@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_cleanup_stops_sys_capture(
    mock_session, mock_rec, mock_sac, mock_bh
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


@patch("scarecrow.sys_audio.find_blackhole_device", return_value=3)
@patch("scarecrow.sys_audio.SystemAudioCapture")
@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_cleanup_clears_sys_capture_ref(
    mock_session, mock_rec, mock_sac, mock_bh
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


@patch("scarecrow.sys_audio.find_blackhole_device", return_value=3)
@patch("scarecrow.sys_audio.SystemAudioCapture")
@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_post_exit_cleanup_compresses_sys_audio(
    mock_session, mock_rec, mock_sac, mock_bh
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
# Footer binding visibility — Quick Quit hidden (Bug 3)
# ---------------------------------------------------------------------------


async def test_quick_quit_binding_is_hidden_from_footer() -> None:
    """Quick Quit binding must have show=False so it is absent from the footer."""
    from scarecrow.app import ScarecrowApp

    hidden = [b for b in ScarecrowApp.BINDINGS if b.action == "quick_quit"]
    assert hidden, "quick_quit binding not found in BINDINGS"
    assert not hidden[0].show, "quick_quit binding should have show=False"


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
# Launch flags: --mic-only / --sys-only (initial mute state)
# ---------------------------------------------------------------------------


@patch("scarecrow.sys_audio.find_blackhole_device", return_value=3)
@patch("scarecrow.sys_audio.SystemAudioCapture")
@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_mic_only_flag_starts_sys_muted(
    mock_session, mock_rec, mock_sac, mock_bh
) -> None:
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


@patch("scarecrow.sys_audio.find_blackhole_device", return_value=3)
@patch("scarecrow.sys_audio.SystemAudioCapture")
@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_sys_only_flag_starts_mic_muted(
    mock_session, mock_rec, mock_sac, mock_bh
) -> None:
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


# ---------------------------------------------------------------------------
# InfoBar click-to-mute
# ---------------------------------------------------------------------------


@patch("scarecrow.sys_audio.find_blackhole_device", return_value=3)
@patch("scarecrow.sys_audio.SystemAudioCapture")
@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_infobar_click_mic_region_toggles_mute(
    mock_session, mock_rec, mock_sac, mock_bh
) -> None:
    """Clicking the mic region of the InfoBar toggles mic mute."""
    mock_sys = _mock_sys_capture()
    mock_sac.return_value = mock_sys
    mock_rec.return_value = _mock_recorder()
    mock_session.return_value = MagicMock()

    async with _sys_app().run_test() as pilot:
        await pilot.press("enter")
        await pilot.pause(delay=0.3)
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        assert app._mic_muted is False
        bar = app.query_one("#info-bar", InfoBar)
        # Regions are set during render; mic region should be non-empty
        mic_s, mic_e = bar._mic_region
        assert mic_e > mic_s, "mic region should be non-empty after render"
        # Simulate click in the mic region
        bar.on_click(MagicMock(x=mic_s, y=0, button=1))
        assert app._mic_muted is True


@patch("scarecrow.sys_audio.find_blackhole_device", return_value=3)
@patch("scarecrow.sys_audio.SystemAudioCapture")
@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_infobar_click_sys_region_toggles_mute(
    mock_session, mock_rec, mock_sac, mock_bh
) -> None:
    """Clicking the sys region of the InfoBar toggles sys mute."""
    mock_sys = _mock_sys_capture()
    mock_sac.return_value = mock_sys
    mock_rec.return_value = _mock_recorder()
    mock_session.return_value = MagicMock()

    async with _sys_app().run_test(size=(120, 24)) as pilot:
        await pilot.press("enter")
        await pilot.pause(delay=0.3)
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        assert app._sys_muted is False
        bar = app.query_one("#info-bar", InfoBar)
        sys_s, sys_e = bar._sys_region
        assert sys_e > sys_s, "sys region should be non-empty with wide terminal"
        bar.on_click(MagicMock(x=sys_s, y=0, button=1))
        assert app._sys_muted is True


# ---------------------------------------------------------------------------
# Mute/unmute transcript events
# ---------------------------------------------------------------------------


@patch("scarecrow.sys_audio.find_blackhole_device", return_value=3)
@patch("scarecrow.sys_audio.SystemAudioCapture")
@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_mute_mic_writes_transcript_event(
    mock_session, mock_rec, mock_sac, mock_bh
) -> None:
    """action_mute_mic() writes a mute event to the session transcript."""
    mock_sys = _mock_sys_capture()
    mock_sac.return_value = mock_sys
    mock_rec.return_value = _mock_recorder()
    session = MagicMock()
    mock_session.return_value = session

    async with _sys_app().run_test() as pilot:
        await pilot.press("enter")
        await pilot.pause(delay=0.3)
        session.append_event.reset_mock()
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        app.action_mute_mic()
        # Find the mute event among calls
        mute_calls = [
            c
            for c in session.append_event.call_args_list
            if c[0][0].get("type") == "mute" and c[0][0].get("source") == "mic"
        ]
        assert len(mute_calls) == 1


@patch("scarecrow.sys_audio.find_blackhole_device", return_value=3)
@patch("scarecrow.sys_audio.SystemAudioCapture")
@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_unmute_sys_writes_transcript_event(
    mock_session, mock_rec, mock_sac, mock_bh
) -> None:
    """Unmuting sys writes an unmute event to the session transcript."""
    mock_sys = _mock_sys_capture()
    mock_sac.return_value = mock_sys
    mock_rec.return_value = _mock_recorder()
    session = MagicMock()
    mock_session.return_value = session

    async with _sys_app().run_test() as pilot:
        await pilot.press("enter")
        await pilot.pause(delay=0.3)
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        app.action_mute_sys()  # mute
        session.append_event.reset_mock()
        app.action_mute_sys()  # unmute
        unmute_calls = [
            c
            for c in session.append_event.call_args_list
            if c[0][0].get("type") == "unmute" and c[0][0].get("source") == "sys"
        ]
        assert len(unmute_calls) == 1


@patch("scarecrow.sys_audio.find_blackhole_device", return_value=3)
@patch("scarecrow.sys_audio.SystemAudioCapture")
@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_launch_flag_mute_writes_transcript_event(
    mock_session, mock_rec, mock_sac, mock_bh
) -> None:
    """--sys-only flag writes initial mute event for mic at recording start."""
    mock_sys = _mock_sys_capture()
    mock_sac.return_value = mock_sys
    mock_rec.return_value = _mock_recorder()
    session = MagicMock()
    mock_session.return_value = session

    async with _sys_app(mic_muted=True).run_test() as pilot:
        await pilot.press("enter")
        await pilot.pause(delay=0.3)
        mute_calls = [
            c
            for c in session.append_event.call_args_list
            if c[0][0].get("type") == "mute" and c[0][0].get("source") == "mic"
        ]
        assert len(mute_calls) == 1


# ---------------------------------------------------------------------------
# Context menu — _handle_context_menu unit tests
# ---------------------------------------------------------------------------


@patch("scarecrow.sys_audio.find_blackhole_device", return_value=3)
@patch("scarecrow.sys_audio.SystemAudioCapture")
@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_context_menu_toggle_mute_mic(
    mock_session, mock_rec, mock_sac, mock_bh
) -> None:
    """Context menu 'toggle_mute' for mic calls action_mute_mic."""
    mock_sac.return_value = _mock_sys_capture()
    mock_rec.return_value = _mock_recorder()
    mock_session.return_value = MagicMock()

    async with _sys_app().run_test() as pilot:
        await pilot.press("enter")
        await pilot.pause(delay=0.3)
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        assert app._mic_muted is False
        app._handle_context_menu("mic:toggle_mute")
        assert app._mic_muted is True


@patch("scarecrow.sys_audio.find_blackhole_device", return_value=3)
@patch("scarecrow.sys_audio.SystemAudioCapture")
@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_context_menu_toggle_mute_sys(
    mock_session, mock_rec, mock_sac, mock_bh
) -> None:
    """Context menu 'toggle_mute' for sys calls action_mute_sys."""
    mock_sac.return_value = _mock_sys_capture()
    mock_rec.return_value = _mock_recorder()
    mock_session.return_value = MagicMock()

    async with _sys_app().run_test() as pilot:
        await pilot.press("enter")
        await pilot.pause(delay=0.3)
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        assert app._sys_muted is False
        app._handle_context_menu("sys:toggle_mute")
        assert app._sys_muted is True


@patch("scarecrow.sys_audio.find_blackhole_device", return_value=3)
@patch("scarecrow.sys_audio.SystemAudioCapture")
@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_context_menu_vad_presets_mic(
    mock_session, mock_rec, mock_sac, mock_bh
) -> None:
    """Each VAD preset mutates the correct mic config fields."""
    mock_sac.return_value = _mock_sys_capture()
    mock_rec.return_value = _mock_recorder()
    mock_session.return_value = MagicMock()

    async with _sys_app().run_test() as pilot:
        await pilot.press("enter")
        await pilot.pause(delay=0.3)
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]

        for preset, (threshold, silence_ms) in ScarecrowApp._VAD_PRESETS.items():
            app._handle_context_menu(f"mic:vad_{preset}")
            assert app._vad_sensitivity == preset
            assert threshold == app._cfg.VAD_SILENCE_THRESHOLD
            assert silence_ms == app._cfg.VAD_MIN_SILENCE_MS


@patch("scarecrow.sys_audio.find_blackhole_device", return_value=3)
@patch("scarecrow.sys_audio.SystemAudioCapture")
@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_context_menu_vad_presets_sys(
    mock_session, mock_rec, mock_sac, mock_bh
) -> None:
    """Each VAD preset mutates the correct sys config fields."""
    mock_sac.return_value = _mock_sys_capture()
    mock_rec.return_value = _mock_recorder()
    mock_session.return_value = MagicMock()

    async with _sys_app().run_test() as pilot:
        await pilot.press("enter")
        await pilot.pause(delay=0.3)
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]

        for preset, (threshold, silence_ms) in ScarecrowApp._SYS_VAD_PRESETS.items():
            app._handle_context_menu(f"sys:vad_{preset}")
            assert app._sys_vad_sensitivity == preset
            assert threshold == app._cfg.SYS_VAD_SILENCE_THRESHOLD
            assert silence_ms == app._cfg.SYS_VAD_MIN_SILENCE_MS


@patch("scarecrow.sys_audio.find_blackhole_device", return_value=3)
@patch("scarecrow.sys_audio.SystemAudioCapture")
@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_context_menu_dismiss_no_action(
    mock_session, mock_rec, mock_sac, mock_bh
) -> None:
    """Dismissing the context menu (None result) does nothing."""
    mock_sac.return_value = _mock_sys_capture()
    mock_rec.return_value = _mock_recorder()
    mock_session.return_value = MagicMock()

    async with _sys_app().run_test() as pilot:
        await pilot.press("enter")
        await pilot.pause(delay=0.3)
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        original_threshold = app._cfg.VAD_SILENCE_THRESHOLD
        app._handle_context_menu(None)
        assert original_threshold == app._cfg.VAD_SILENCE_THRESHOLD
        assert app._mic_muted is False


@patch("scarecrow.sys_audio.find_blackhole_device", return_value=3)
@patch("scarecrow.sys_audio.SystemAudioCapture")
@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_context_menu_push_select_dismiss(
    mock_session, mock_rec, mock_sac, mock_bh
) -> None:
    """Pushing the context menu, selecting an option, and dismissing does not crash."""
    mock_sac.return_value = _mock_sys_capture()
    mock_rec.return_value = _mock_recorder()
    mock_session.return_value = MagicMock()

    async with _sys_app().run_test() as pilot:
        await pilot.press("enter")
        await pilot.pause(delay=0.3)
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        assert app._mic_muted is False

        # Open the combined menu via action (same as Ctrl+V binding)
        app.action_vad_menu()
        await pilot.pause(delay=0.3)
        assert any(isinstance(s, ContextMenuScreen) for s in app.screen_stack), (
            "ContextMenuScreen should be on the stack"
        )

        # Move past the header and select Mute Mic
        option_list = app.screen.query_one(OptionList)
        option_list.action_cursor_down()
        option_list.action_select()
        await pilot.pause(delay=0.3)

        # Screen should be dismissed without crash
        assert not any(isinstance(s, ContextMenuScreen) for s in app.screen_stack), (
            "ContextMenuScreen should be dismissed"
        )


@patch("scarecrow.sys_audio.find_blackhole_device", return_value=3)
@patch("scarecrow.sys_audio.SystemAudioCapture")
@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_context_menu_escape_dismiss(
    mock_session, mock_rec, mock_sac, mock_bh
) -> None:
    """Pressing Escape dismisses the context menu without action."""
    mock_sac.return_value = _mock_sys_capture()
    mock_rec.return_value = _mock_recorder()
    mock_session.return_value = MagicMock()

    async with _sys_app().run_test() as pilot:
        await pilot.press("enter")
        await pilot.pause(delay=0.3)
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]

        app.action_vad_menu()
        await pilot.pause(delay=0.3)
        assert any(isinstance(s, ContextMenuScreen) for s in app.screen_stack)

        await pilot.press("escape")
        await pilot.pause(delay=0.3)
        assert not any(isinstance(s, ContextMenuScreen) for s in app.screen_stack), (
            "Escape should dismiss the menu"
        )
        assert app._mic_muted is False  # no action taken


@patch("scarecrow.sys_audio.find_blackhole_device", return_value=3)
@patch("scarecrow.sys_audio.SystemAudioCapture")
@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_non_left_click_ignored(
    mock_session, mock_rec, mock_sac, mock_bh
) -> None:
    """Non-left-click on the mic region is ignored (no mute toggle)."""
    mock_sac.return_value = _mock_sys_capture()
    mock_rec.return_value = _mock_recorder()
    mock_session.return_value = MagicMock()

    async with _sys_app().run_test() as pilot:
        await pilot.press("enter")
        await pilot.pause(delay=0.3)
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        bar = app.query_one("#info-bar", InfoBar)
        mic_s, mic_e = bar._mic_region
        assert mic_e > mic_s
        bar.on_click(MagicMock(x=mic_s, y=0, button=3))
        assert app._mic_muted is False


# ---------------------------------------------------------------------------
# Auto-segmentation — _check_segment_boundary
# ---------------------------------------------------------------------------


@patch("scarecrow.sys_audio.find_blackhole_device", return_value=3)
@patch("scarecrow.sys_audio.SystemAudioCapture")
@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_check_segment_boundary_triggers_at_threshold(
    mock_session, mock_rec, mock_sac, mock_bh
) -> None:
    """_check_segment_boundary increments _current_segment at the boundary."""
    mock_sac.return_value = _mock_sys_capture()
    mock_rec.return_value = _mock_recorder()
    mock_session.return_value = MagicMock()

    async with _sys_app().run_test() as pilot:
        await pilot.press("enter")
        await pilot.pause(delay=0.3)
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        assert app._current_segment == 1
        # Simulate elapsed time past the boundary
        app._elapsed = app._cfg.SEGMENT_DURATION_SECONDS + 1
        app._check_segment_boundary()
        assert app._current_segment == 2


@patch("scarecrow.sys_audio.find_blackhole_device", return_value=3)
@patch("scarecrow.sys_audio.SystemAudioCapture")
@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_check_segment_boundary_skips_when_paused(
    mock_session, mock_rec, mock_sac, mock_bh
) -> None:
    """_check_segment_boundary does nothing when state is PAUSED."""
    mock_sac.return_value = _mock_sys_capture()
    mock_rec.return_value = _mock_recorder()
    mock_session.return_value = MagicMock()

    async with _sys_app().run_test() as pilot:
        await pilot.press("enter")
        await pilot.pause(delay=0.3)
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        app.state = AppState.PAUSED
        app._elapsed = app._cfg.SEGMENT_DURATION_SECONDS + 1
        app._check_segment_boundary()
        assert app._current_segment == 1  # No rotation while paused


@patch("scarecrow.sys_audio.find_blackhole_device", return_value=3)
@patch("scarecrow.sys_audio.SystemAudioCapture")
@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_check_segment_boundary_no_trigger_before_threshold(
    mock_session, mock_rec, mock_sac, mock_bh
) -> None:
    """_check_segment_boundary does nothing before the duration is reached."""
    mock_sac.return_value = _mock_sys_capture()
    mock_rec.return_value = _mock_recorder()
    mock_session.return_value = MagicMock()

    async with _sys_app().run_test() as pilot:
        await pilot.press("enter")
        await pilot.pause(delay=0.3)
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        app._elapsed = app._cfg.SEGMENT_DURATION_SECONDS - 1
        app._check_segment_boundary()
        assert app._current_segment == 1
