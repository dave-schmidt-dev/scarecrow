"""Behavioral contract tests — lock in working behavior before refactor.

These tests verify observable contracts, not implementation details.
"""

from __future__ import annotations

import math
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from textual.widgets import RichLog

from scarecrow.app import (
    BATCH_INTERVAL_SECONDS,
    AppState,
    AudioMeter,
    InfoBar,
    ScarecrowApp,
)

# ---------------------------------------------------------------------------
# Helpers (mirrors test_app.py pattern — no shared state between modules)
# ---------------------------------------------------------------------------


def _mock_transcriber():
    """Return a mock Transcriber that doesn't load models."""
    mock = MagicMock()
    mock.is_ready = True
    mock.text.side_effect = StopIteration
    mock.set_callbacks.return_value = None
    mock.shutdown.return_value = None
    return mock


def _mock_recorder():
    """Return a mock AudioRecorder that doesn't touch hardware."""
    mock = MagicMock()
    mock.is_recording = True
    mock.is_paused = False
    mock.peak_level = 0.0
    mock.start.return_value = None
    mock.stop.return_value = MagicMock()
    return mock


def _app(with_transcriber: bool = False) -> ScarecrowApp:
    if with_transcriber:
        return ScarecrowApp(transcriber=_mock_transcriber())
    return ScarecrowApp()


# ---------------------------------------------------------------------------
# 1. AudioMeter renders with dB scale
# ---------------------------------------------------------------------------


def test_audio_meter_zero_level_renders_zero_bars() -> None:
    """level=0.0 must produce zero filled bars in the rendered text."""
    meter = AudioMeter()
    meter._reactive_level = 0.0  # set reactive directly without running app
    # Render directly — the meter has no app context needed for render()
    rendered = meter.render()
    text_plain = rendered.plain
    # All 20 slots should be light-shade (░), none filled (█)
    assert "\u2588" not in text_plain, "Zero level must produce no filled bars"
    assert "\u2591" in text_plain, "Zero level must produce empty-bar characters"


@pytest.mark.parametrize("linear_level", [0.01, 0.02, 0.05])
def test_audio_meter_speech_levels_produce_visible_bars(linear_level: float) -> None:
    """Typical speech levels (0.01-0.05 linear) must produce at least 1 filled bar.

    The dB scale maps -60dB..0dB to 0..100%, so 0.01 linear ~= -40dB ~= 33%.
    That should yield several visible bars -- not zero.
    """
    meter = AudioMeter()
    meter._reactive_level = linear_level

    rendered = meter.render()
    text_plain = rendered.plain

    assert "\u2588" in text_plain, (
        f"Speech-level input {linear_level} must produce at least one filled bar"
    )


def test_audio_meter_db_scale_math_is_correct() -> None:
    """Verify the dB → bar count math matches the expected formula."""
    # level=0.01 → db = 20*log10(0.01) = -40dB
    # normalized = (-40 + 60) / 60 = 20/60 ≈ 0.333
    # bars = int(0.333 * 20) = 6
    level = 0.01
    db = 20 * math.log10(level)
    normalized = max(0.0, min(1.0, (db + 60) / 60))
    expected_bars = int(normalized * 20)
    assert expected_bars >= 1, "Math should yield at least 1 bar for 0.01 linear"

    meter = AudioMeter()
    meter._reactive_level = level
    rendered = meter.render()
    filled_count = rendered.plain.count("\u2588")
    assert filled_count == expected_bars


# ---------------------------------------------------------------------------
# 2. RichLog widgets are created with wrap=True and min_width=0
# ---------------------------------------------------------------------------


async def test_captions_richlog_has_wrap_and_min_width() -> None:
    """#captions RichLog must be created with wrap=True and min_width=0."""
    async with _app().run_test() as pilot:
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        captions = app.query_one("#captions", RichLog)
        assert captions.wrap is True
        assert captions.min_width == 0


async def test_live_log_richlog_has_wrap_and_min_width() -> None:
    """#live-log RichLog must be created with wrap=True and min_width=0."""
    async with _app().run_test() as pilot:
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        live_log = app.query_one("#live-log", RichLog)
        assert live_log.wrap is True
        assert live_log.min_width == 0


# ---------------------------------------------------------------------------
# 3. Batch divider appears in transcript with timestamp and path
# ---------------------------------------------------------------------------


async def test_append_transcript_writes_divider_with_timestamp_and_path() -> None:
    """_append_transcript must write a divider line containing the timestamp
    and session transcript path before the text."""
    async with _app().run_test() as pilot:
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        await pilot.pause()

        # Set up a mock session with a known transcript path
        mock_session = MagicMock()
        mock_session.transcript_path = Path("/tmp/test_session.txt")
        app._session = mock_session
        app._elapsed = 125  # 00:02:05

        captions = app.query_one("#captions", RichLog)
        initial_lines = len(captions.lines)

        app._append_transcript("Some transcribed text.")
        await pilot.pause()

        # Two new lines: divider + text
        assert len(captions.lines) >= initial_lines + 2

        # Collect all rendered plain text from the new lines
        new_lines_text = " ".join(str(line) for line in captions.lines[initial_lines:])
        assert "00:02:05" in new_lines_text, "Divider must contain timestamp"
        assert "test_session.txt" in new_lines_text, "Divider must contain path"


async def test_append_transcript_no_divider_without_session() -> None:
    """_append_transcript without a session must write only the text (no divider)."""
    async with _app().run_test() as pilot:
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        await pilot.pause()

        app._session = None
        captions = app.query_one("#captions", RichLog)

        app._append_transcript("Just the text.")
        app._append_transcript("Second line.")
        await pilot.pause()

        # With no session, dividers are skipped — lines should contain
        # our text without interleaved divider lines
        texts = [str(line) for line in captions.lines]
        text_lines = [t for t in texts if "Just the text" in t or "Second line" in t]
        # Should be exactly 2 text entries with no dividers between
        assert len(text_lines) == 2


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

    text = bar.render().plain
    assert "17s" in text
    assert "batch" in text


def test_info_bar_hides_batch_countdown_when_idle() -> None:
    """InfoBar must NOT show batch countdown when not recording."""
    bar = InfoBar()
    bar._reactive_state = AppState.IDLE
    bar._reactive_elapsed = 0
    bar._reactive_word_count = 0
    bar._reactive_batch_countdown = 17

    text = bar.render().plain
    assert "batch" not in text


def test_info_bar_renders_recording_state_label() -> None:
    """InfoBar in RECORDING state must show 'REC' label."""
    bar = InfoBar()
    bar._reactive_state = AppState.RECORDING
    bar._reactive_elapsed = 0
    bar._reactive_word_count = 0
    bar._reactive_batch_countdown = BATCH_INTERVAL_SECONDS

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


# ---------------------------------------------------------------------------
# 6. Shutdown messages appear in the live log
# ---------------------------------------------------------------------------


@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_shutdown_writes_shutting_down_message(
    mock_session_cls, mock_recorder_cls
) -> None:
    """action_quit must pass 'Shutting down' to _update_live.

    _update_live clears and rewrites the live pane each call, so the final
    state only shows the last message. We verify the sequence by tracking
    all calls to _update_live instead of reading the widget after-the-fact.
    """
    mock_recorder_cls.return_value = _mock_recorder()
    mock_session_cls.return_value = MagicMock()

    async with _app(with_transcriber=True).run_test() as pilot:
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        await pilot.pause(delay=0.5)

        messages: list[str] = []
        original_update_live = app._update_live

        def track_live(text: str) -> None:
            messages.append(text)
            original_update_live(text)

        app._update_live = track_live  # type: ignore[method-assign]

        # Suppress the deferred quit so the app doesn't close under us
        with patch.object(app, "_deferred_quit"):
            app.action_quit()
            await pilot.pause(delay=0.1)

        assert any("Shutting down" in m for m in messages), (
            f"Expected 'Shutting down' in live pane messages; got: {messages}"
        )


@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_stop_recording_transitions_to_idle(
    mock_session_cls, mock_recorder_cls
) -> None:
    """_stop_recording must transition state to IDLE and stop recorder."""
    mock_rec = _mock_recorder()
    mock_recorder_cls.return_value = mock_rec
    mock_session_cls.return_value = MagicMock()

    async with _app(with_transcriber=True).run_test() as pilot:
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        await pilot.pause(delay=0.5)
        assert app.state is AppState.RECORDING

        app._stop_recording()
        await pilot.pause()

        assert app.state is AppState.IDLE
        mock_rec.stop.assert_called_once()


@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_stop_recording_finalizes_session(
    mock_session_cls, mock_recorder_cls
) -> None:
    """_stop_recording must finalize the session."""
    mock_session = MagicMock()
    mock_recorder_cls.return_value = _mock_recorder()
    mock_session_cls.return_value = mock_session

    async with _app(with_transcriber=True).run_test() as pilot:
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        await pilot.pause(delay=0.5)

        app._stop_recording()
        await pilot.pause()

        mock_session.finalize.assert_called_once()


# ---------------------------------------------------------------------------
# 7. _append_transcript increments word count correctly
# ---------------------------------------------------------------------------


async def test_append_transcript_increments_word_count_by_word_count() -> None:
    """_append_transcript must add exactly len(text.split()) to _word_count."""
    async with _app().run_test() as pilot:
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        await pilot.pause()

        app._session = None
        assert app._word_count == 0

        app._append_transcript("one two three four five")
        await pilot.pause()
        assert app._word_count == 5


async def test_append_transcript_accumulates_across_calls() -> None:
    """Successive calls to _append_transcript must accumulate word counts."""
    async with _app().run_test() as pilot:
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        await pilot.pause()

        app._session = None
        app._append_transcript("hello world")
        app._append_transcript("one two three")
        await pilot.pause()
        assert app._word_count == 5


async def test_append_transcript_syncs_info_bar() -> None:
    """_append_transcript must sync the InfoBar with the new word count."""
    async with _app().run_test() as pilot:
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        await pilot.pause()

        app._session = None
        app._append_transcript("alpha beta gamma")
        await pilot.pause()

        bar = app.query_one(InfoBar)
        assert bar.word_count == 3


# ---------------------------------------------------------------------------
# 8. _on_batch_tick resets countdown to BATCH_INTERVAL_SECONDS
# ---------------------------------------------------------------------------


async def test_on_batch_tick_resets_countdown() -> None:
    """_on_batch_tick must reset _batch_countdown to BATCH_INTERVAL_SECONDS."""
    async with _app().run_test() as pilot:
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        await pilot.pause()

        # Drive countdown down to simulate elapsed time
        app._batch_countdown = 5
        app._on_batch_tick()
        await pilot.pause()

        assert app._batch_countdown == BATCH_INTERVAL_SECONDS


async def test_on_batch_tick_syncs_info_bar() -> None:
    """_on_batch_tick must update InfoBar batch_countdown to BATCH_INTERVAL_SECONDS."""
    async with _app().run_test() as pilot:
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        await pilot.pause()

        app._batch_countdown = 1
        app.state = AppState.RECORDING  # so InfoBar shows countdown
        app._on_batch_tick()
        await pilot.pause()

        bar = app.query_one(InfoBar)
        assert bar.batch_countdown == BATCH_INTERVAL_SECONDS


# ---------------------------------------------------------------------------
# 9. peak_level property exists on AudioRecorder and starts at 0.0
# ---------------------------------------------------------------------------


def test_audio_recorder_peak_level_property_exists(tmp_path: Path) -> None:
    """AudioRecorder must expose a peak_level property."""
    from scarecrow.recorder import AudioRecorder

    with patch("scarecrow.recorder.sd"), patch("scarecrow.recorder.sf"):
        recorder = AudioRecorder(tmp_path / "audio.wav")
        assert hasattr(recorder, "peak_level")


def test_audio_recorder_peak_level_starts_at_zero(tmp_path: Path) -> None:
    """AudioRecorder.peak_level must be 0.0 before any audio is received."""
    from scarecrow.recorder import AudioRecorder

    with patch("scarecrow.recorder.sd"), patch("scarecrow.recorder.sf"):
        recorder = AudioRecorder(tmp_path / "audio.wav")
        assert recorder.peak_level == 0.0


def test_audio_recorder_peak_level_is_float(tmp_path: Path) -> None:
    """AudioRecorder.peak_level must be a float, not an int or None."""
    from scarecrow.recorder import AudioRecorder

    with patch("scarecrow.recorder.sd"), patch("scarecrow.recorder.sf"):
        recorder = AudioRecorder(tmp_path / "audio.wav")
        assert isinstance(recorder.peak_level, float)


# ---------------------------------------------------------------------------
# 10. Deferred quit pattern — action_quit uses timer, not direct exit
# ---------------------------------------------------------------------------


async def test_action_quit_does_not_call_exit_immediately() -> None:
    """action_quit must NOT call exit() synchronously; it defers via set_timer."""
    async with _app().run_test() as pilot:
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        await pilot.pause()

        exit_calls: list[bool] = []
        original_exit = app.exit

        def tracking_exit(*args, **kwargs):
            exit_calls.append(True)
            return original_exit(*args, **kwargs)

        app.exit = tracking_exit  # type: ignore[method-assign]

        # Call action_quit but don't wait long enough for the deferred timer
        app.action_quit()
        # exit should NOT have been called yet — it's deferred by 0.05s
        assert len(exit_calls) == 0, (
            "action_quit must defer exit via set_timer, not call it synchronously"
        )


async def test_action_quit_writes_shutting_down_to_live_pane() -> None:
    """action_quit must immediately write 'Shutting down' to live pane
    (before the deferred exit fires), confirming the deferred pattern."""
    async with _app().run_test() as pilot:
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        await pilot.pause()

        # Suppress the deferred exit so we can inspect state
        with patch.object(app, "_deferred_quit"):
            app.action_quit()
            await pilot.pause()

        live_log = app.query_one("#live-log", RichLog)
        lines_text = " ".join(str(line) for line in live_log.lines)
        assert "Shutting down" in lines_text


async def test_deferred_quit_calls_stop_recording_then_exit() -> None:
    """_deferred_quit must call _stop_recording and then exit()."""
    async with _app().run_test() as pilot:
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        await pilot.pause()

        stop_called: list[bool] = []
        exit_called: list[bool] = []

        original_exit = app.exit

        def track_stop():
            stop_called.append(True)
            # Don't actually stop — no recorder running
            pass

        def track_exit(*args, **kwargs):
            exit_called.append(True)
            return original_exit(*args, **kwargs)

        app._stop_recording = track_stop  # type: ignore[method-assign]
        app.exit = track_exit  # type: ignore[method-assign]

        app._deferred_quit()
        await pilot.pause()

        assert len(stop_called) == 1, "_deferred_quit must call _stop_recording"
        assert len(exit_called) == 1, "_deferred_quit must call exit"
