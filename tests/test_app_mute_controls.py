"""Mic and sys-audio mute/unmute control tests."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from scarecrow.app import AppState, InfoBar, ScarecrowApp
from tests.helpers import _mock_recorder, _mock_sys_capture, _mock_transcriber, _sys_app

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
