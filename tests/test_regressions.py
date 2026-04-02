"""Regression tests for bugs found during testing sessions."""

from unittest.mock import patch

# ---------------------------------------------------------------------------
# Bug: shutdown metrics not visible — TUI exits too fast.
# Metrics must be saved to app._shutdown_summary for __main__.py to print.
# ---------------------------------------------------------------------------


async def test_shutdown_summary_saved_on_quit() -> None:
    """action_quit must save _shutdown_summary before exiting."""
    from scarecrow.app import ScarecrowApp

    async with ScarecrowApp().run_test() as pilot:
        app = pilot.app
        with patch.object(app, "_deferred_quit"):
            app.action_quit()
            await pilot.pause()
        assert hasattr(app, "_shutdown_summary")
        assert "Duration" in app._shutdown_summary
        assert "Words" in app._shutdown_summary
