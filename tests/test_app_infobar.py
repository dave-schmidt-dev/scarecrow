"""InfoBar rendering and widget configuration tests."""

from __future__ import annotations

from textual.widgets import RichLog

from scarecrow.app import BATCH_INTERVAL_SECONDS, AppState, InfoBar, ScarecrowApp
from tests.helpers import _app

# ---------------------------------------------------------------------------
# 1. RichLog widgets are created with wrap=True and min_width=0
# ---------------------------------------------------------------------------


async def test_captions_richlog_has_wrap_and_min_width() -> None:
    """#captions RichLog must be created with wrap=True and min_width=0."""
    async with _app().run_test() as pilot:
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        captions = app.query_one("#captions", RichLog)
        assert captions.wrap is True
        assert captions.min_width == 0


# ---------------------------------------------------------------------------
# 4. Command palette is disabled
# ---------------------------------------------------------------------------


def test_enable_command_palette_is_false() -> None:
    """ENABLE_COMMAND_PALETTE must be False on ScarecrowApp."""
    assert ScarecrowApp.ENABLE_COMMAND_PALETTE is False


# ---------------------------------------------------------------------------
# 5. InfoBar shows word count, elapsed time, state, batch countdown
# ---------------------------------------------------------------------------


def test_info_bar_renders_idle_state() -> None:
    """InfoBar in IDLE renders the state label and time."""
    bar = InfoBar()
    bar._reactive_state = AppState.IDLE
    bar._reactive_elapsed = 0
    bar._reactive_word_count = 0
    bar._reactive_batch_countdown = BATCH_INTERVAL_SECONDS

    text = bar.render().plain
    assert "IDLE" in text
    assert "00:00:00" in text
    assert "0" in text  # word count
    assert "words" in text


def test_info_bar_renders_word_count() -> None:
    """InfoBar must display the current word count."""
    bar = InfoBar()
    bar._reactive_state = AppState.RECORDING
    bar._reactive_elapsed = 0
    bar._reactive_word_count = 42
    bar._reactive_batch_countdown = BATCH_INTERVAL_SECONDS
    bar._reactive_peak_level = 0.0

    text = bar.render().plain
    assert "42" in text
    assert "words" in text


def test_info_bar_renders_elapsed_time() -> None:
    """InfoBar must format elapsed seconds as HH:MM:SS."""
    bar = InfoBar()
    bar._reactive_state = AppState.IDLE
    bar._reactive_elapsed = 3661  # 1h 1m 1s
    bar._reactive_word_count = 0
    bar._reactive_batch_countdown = BATCH_INTERVAL_SECONDS

    text = bar.render().plain
    assert "01:01:01" in text


def test_info_bar_renders_batch_countdown_when_recording() -> None:
    """InfoBar must show the batch countdown only when in RECORDING state."""
    bar = InfoBar()
    bar._reactive_state = AppState.RECORDING
    bar._reactive_elapsed = 0
    bar._reactive_word_count = 0
    bar._reactive_batch_countdown = 17
    bar._reactive_peak_level = 0.0

    text = bar.render().plain
    assert "17s" in text
    assert "batch" in text or "buf" in text


def test_info_bar_hides_batch_countdown_when_idle() -> None:
    """InfoBar must NOT show batch countdown when not recording."""
    bar = InfoBar()
    bar._reactive_state = AppState.IDLE
    bar._reactive_elapsed = 0
    bar._reactive_word_count = 0
    bar._reactive_batch_countdown = 17

    text = bar.render().plain
    assert "batch" not in text


async def test_tick_does_not_decrement_batch_countdown() -> None:
    """_tick must not decrement _batch_countdown — only the VAD poll sets it.

    Regression: BUG-20260328-buffer-time-jitter. The 1-second tick was
    decrementing the countdown independently, fighting the VAD poll and
    causing visible jitter in the buffer time display.
    """
    async with _app().run_test() as pilot:
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        app._batch_countdown = 10
        app._recording_start_time = None
        app._audio_recorder = None
        app._tick()
        assert app._batch_countdown == 10


def test_info_bar_renders_recording_state_label() -> None:
    """InfoBar in RECORDING state must show 'REC' label."""
    bar = InfoBar()
    bar._reactive_state = AppState.RECORDING
    bar._reactive_elapsed = 0
    bar._reactive_word_count = 0
    bar._reactive_batch_countdown = BATCH_INTERVAL_SECONDS
    bar._reactive_peak_level = 0.0

    text = bar.render().plain
    assert "REC" in text


def test_info_bar_renders_paused_state_label() -> None:
    """InfoBar in PAUSED state must show 'PAUSED' label."""
    bar = InfoBar()
    bar._reactive_state = AppState.PAUSED
    bar._reactive_elapsed = 0
    bar._reactive_word_count = 0
    bar._reactive_batch_countdown = BATCH_INTERVAL_SECONDS

    text = bar.render().plain
    assert "PAUSED" in text


def test_info_bar_peak_level_renders() -> None:
    """InfoBar in RECORDING state must render an audio level bar character."""
    bar = InfoBar()
    bar._reactive_state = AppState.RECORDING
    bar._reactive_elapsed = 0
    bar._reactive_word_count = 0
    bar._reactive_batch_countdown = BATCH_INTERVAL_SECONDS
    bar._reactive_peak_level = 0.5

    text = bar.render().plain
    bars = "▁▂▃▄▅▆▇█"
    assert any(ch in text for ch in bars)
