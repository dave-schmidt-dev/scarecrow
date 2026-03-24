"""Tests for the Scarecrow Textual TUI (Phase 5 — UI only)."""

from __future__ import annotations

from scarecrow.app import AppState, ScarecrowApp

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _app() -> ScarecrowApp:
    return ScarecrowApp()


# ---------------------------------------------------------------------------
# Launch
# ---------------------------------------------------------------------------


async def test_app_launches() -> None:
    """App starts without errors."""
    async with _app().run_test() as pilot:
        await pilot.pause()
        # If we reach here, the app launched successfully.
        assert pilot.app is not None


# ---------------------------------------------------------------------------
# Initial state
# ---------------------------------------------------------------------------


async def test_initial_state_is_idle() -> None:
    async with _app().run_test() as pilot:
        await pilot.pause()
        assert pilot.app.state is AppState.IDLE  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Recording transitions
# ---------------------------------------------------------------------------


async def test_press_r_starts_recording() -> None:
    async with _app().run_test() as pilot:
        await pilot.press("r")
        assert pilot.app.state is AppState.RECORDING  # type: ignore[attr-defined]


async def test_press_p_during_recording_pauses() -> None:
    async with _app().run_test() as pilot:
        await pilot.press("r")
        await pilot.press("p")
        assert pilot.app.state is AppState.PAUSED  # type: ignore[attr-defined]


async def test_press_p_during_paused_resumes() -> None:
    async with _app().run_test() as pilot:
        await pilot.press("r")
        await pilot.press("p")
        await pilot.press("p")
        assert pilot.app.state is AppState.RECORDING  # type: ignore[attr-defined]


async def test_press_r_while_recording_is_noop() -> None:
    async with _app().run_test() as pilot:
        await pilot.press("r")
        assert pilot.app.state is AppState.RECORDING  # type: ignore[attr-defined]
        await pilot.press("r")
        # State must still be RECORDING, not reset to IDLE or anything else.
        assert pilot.app.state is AppState.RECORDING  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Elapsed timer
# ---------------------------------------------------------------------------


async def test_timer_starts_on_record() -> None:
    async with _app().run_test() as pilot:
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        assert app._elapsed == 0
        await pilot.press("r")
        # Advance time by 2 seconds via Textual's test clock
        await pilot.pause(delay=2)
        assert app._elapsed >= 1


async def test_timer_pauses_when_paused() -> None:
    async with _app().run_test() as pilot:
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        await pilot.press("r")
        await pilot.pause(delay=1)
        await pilot.press("p")
        elapsed_at_pause = app._elapsed
        await pilot.pause(delay=2)
        # Elapsed must not increase while paused.
        assert app._elapsed == elapsed_at_pause


# ---------------------------------------------------------------------------
# Quit
# ---------------------------------------------------------------------------


async def test_press_q_exits() -> None:
    app = _app()
    async with app.run_test() as pilot:
        await pilot.press("q")
    # If we reach here the app exited cleanly.
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
        # Live preview should now be empty.
        preview = app.query_one("#live-preview", Static)
        assert str(preview.render()).strip() == ""
        # RichLog should contain the settled text.
        log = app.query_one("#captions", RichLog)
        assert len(log.lines) >= 1
