"""Context menu, click-to-mute, and mute transcript event tests."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from textual.widgets import OptionList

from scarecrow.app import ContextMenuScreen, InfoBar, ScarecrowApp
from tests.helpers import _mock_recorder, _mock_sys_capture, _sys_app

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

        for preset, gain in ScarecrowApp._MIC_PRESETS.items():
            app._handle_context_menu(f"mic:vad_{preset}")
            assert app._vad_sensitivity == preset
            assert gain == app._cfg.MIC_GAIN


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

        for preset, gain in ScarecrowApp._SYS_PRESETS.items():
            app._handle_context_menu(f"sys:vad_{preset}")
            assert app._sys_vad_sensitivity == preset
            assert gain == app._cfg.SYS_GAIN


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
