"""System audio VAD and auto-segmentation tests."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np

from scarecrow.app import AppState, ScarecrowApp
from tests.helpers import _mock_recorder, _mock_sys_capture, _mock_transcriber, _sys_app

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
        # Rotation is now async — wait for timers to fire
        assert app._rotation_pending is True
        await pilot.pause(delay=1.0)
        assert app._current_segment == 2
        assert app._rotation_pending is False


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
