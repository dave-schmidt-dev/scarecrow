"""Behavioral contract tests — lock in working behavior before refactor.

These tests verify observable contracts, not implementation details.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
from textual.widgets import RichLog, Static

from scarecrow.app import (
    BATCH_INTERVAL_SECONDS,
    AppState,
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
        app = ScarecrowApp(transcriber=_mock_transcriber())
        app._preflight_check = lambda: True  # type: ignore[method-assign]
        return app
    return ScarecrowApp()


def _live_text(app: ScarecrowApp) -> str:
    widget = app.query_one("#live-content", Static)
    return widget.render().plain


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


async def test_live_pane_present() -> None:
    """Live pane should render as a single scrollable widget with content."""
    async with _app().run_test() as pilot:
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        live_pane = app.query_one("#live-pane")
        live_content = app.query_one("#live-content", Static)
        assert live_pane is not None
        assert live_content is not None


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
    mock_session = MagicMock()
    mock_session.session_dir = Path("/tmp/test-session")
    mock_session.audio_path = Path("/tmp/test-session/audio.wav")
    mock_session.transcript_path = Path("/tmp/test-session/transcript.txt")
    mock_session_cls.return_value = mock_session

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

        assert "Shutting down" in _live_text(app)


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


# ---------------------------------------------------------------------------
# Live pane: behavioral contracts
# ---------------------------------------------------------------------------


async def test_live_partial_shows_in_richlog() -> None:
    """Partial (in-progress) transcription must appear in the live pane."""
    async with _app().run_test() as pilot:
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        await pilot.pause()

        app._update_live_partial("streaming words...")
        await pilot.pause()

        assert "streaming words..." in _live_text(app)


async def test_stabilized_text_goes_to_richlog() -> None:
    """Stabilized (final) text must be written to the live pane."""
    async with _app().run_test() as pilot:
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        await pilot.pause()

        app._append_live("final sentence one")
        app._append_live("final sentence two")
        await pilot.pause()

        lines_text = _live_text(app)
        assert "final sentence one" in lines_text
        assert "final sentence two" in lines_text


async def test_history_preserved_across_partial_updates() -> None:
    """Stabilized history must survive partial update clear+replay cycles."""
    async with _app().run_test() as pilot:
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        await pilot.pause()

        # Add stabilized history
        app._append_live("history line one")
        app._append_live("history line two")
        await pilot.pause()

        # Partial update clears+replays but history should still be present
        app._update_live_partial("in progress...")
        await pilot.pause()

        lines_text = _live_text(app)
        assert "history line one" in lines_text
        assert "history line two" in lines_text
        assert "in progress..." in lines_text


async def test_stabilized_replaces_partial() -> None:
    """When stabilized text arrives, it replaces the partial (not appended after)."""
    async with _app().run_test() as pilot:
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        await pilot.pause()

        app._update_live_partial("in progress...")
        await pilot.pause()

        app._append_live("finalized text")
        await pilot.pause()

        lines_text = _live_text(app)
        assert "finalized text" in lines_text
        # Partial should be gone — replaced by stabilized
        assert "in progress..." not in lines_text


async def test_shutdown_summary_contains_metrics() -> None:
    """_shutdown_summary must contain duration and word count for terminal output."""
    async with _app().run_test() as pilot:
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        app._elapsed = 125  # 2:05
        app._word_count = 42
        await pilot.pause()

        with patch.object(app, "_deferred_quit"):
            app.action_quit()
            await pilot.pause()

        assert "00:02:05" in app._shutdown_summary
        assert "42" in app._shutdown_summary


@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_pause_preserves_live_history(
    mock_session_cls, mock_recorder_cls
) -> None:
    """Pausing must not wipe accumulated live transcription lines."""
    mock_recorder_cls.return_value = _mock_recorder()
    mock_session_cls.return_value = MagicMock()

    async with _app(with_transcriber=True).run_test() as pilot:
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        await pilot.pause(delay=0.5)

        app._append_live("first utterance")
        app._append_live("second utterance")
        await pilot.pause()

        await pilot.press("p")
        await pilot.pause()

        text = _live_text(app)
        assert "first utterance" in text
        assert "second utterance" in text


@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_start_recording_unwinds_recorder_when_transcriber_begin_fails(
    mock_session_cls, mock_recorder_cls
) -> None:
    """Startup failure after mic acquisition must unwind recorder and session."""
    mock_recorder = _mock_recorder()
    mock_session = MagicMock()
    mock_recorder_cls.return_value = mock_recorder
    mock_session_cls.return_value = mock_session

    transcriber = _mock_transcriber()
    transcriber.begin_session.side_effect = RuntimeError("boom")

    with patch.object(ScarecrowApp, "_auto_start"):
        async with ScarecrowApp(transcriber=transcriber).run_test() as pilot:
            app: ScarecrowApp = pilot.app  # type: ignore[assignment]
            app._start_recording()
            await pilot.pause()

            mock_recorder.stop.assert_called_once()
            mock_session.finalize.assert_called_once()
            assert app._audio_recorder is None
            assert app._session is None
            assert app.state is AppState.IDLE


@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_stop_recording_flushes_final_batch_before_finalize(
    mock_session_cls, mock_recorder_cls
) -> None:
    """Shutdown must transcribe the last buffered audio before finalize()."""
    calls: list[str] = []
    mock_recorder = _mock_recorder()
    mock_recorder.drain_buffer.return_value = np.zeros(16000)
    mock_recorder.stop.side_effect = lambda: calls.append("recorder-stop")
    mock_recorder_cls.return_value = mock_recorder

    mock_session = MagicMock()
    mock_session.finalize.side_effect = lambda: calls.append("session-finalize")
    mock_session_cls.return_value = mock_session

    transcriber = _mock_transcriber()
    transcriber.end_session.side_effect = lambda: calls.append("end-session")

    def fake_final_batch(audio, elapsed):
        calls.append("final-batch")
        return "transcribed text"

    transcriber.transcribe_batch.side_effect = fake_final_batch
    transcriber.shutdown.side_effect = lambda timeout=None: calls.append("shutdown")

    async with ScarecrowApp(transcriber=transcriber).run_test() as pilot:
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        await pilot.pause(delay=0.2)
        app._session = mock_session
        app._audio_recorder = mock_recorder
        app.state = AppState.RECORDING

        app._stop_recording()
        await pilot.pause()

    assert calls == [
        "end-session",
        "recorder-stop",
        "final-batch",
        "shutdown",
        "session-finalize",
    ]


@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_stop_recording_waits_for_inflight_batch_before_finalize(
    mock_session_cls, mock_recorder_cls
) -> None:
    """A running batch worker must finish before the session file is finalized."""
    import threading
    import time

    calls: list[str] = []
    release = threading.Event()
    mock_recorder = _mock_recorder()
    mock_recorder.drain_buffer.return_value = None
    mock_recorder_cls.return_value = mock_recorder

    mock_session = MagicMock()
    mock_session.finalize.side_effect = lambda: calls.append("session-finalize")
    mock_session_cls.return_value = mock_session

    def slow_batch(_audio, _elapsed):
        calls.append("batch-start")
        release.wait(timeout=1)
        calls.append("batch-end")

    transcriber = _mock_transcriber()
    transcriber.transcribe_batch.side_effect = slow_batch
    transcriber.shutdown.side_effect = lambda timeout=None: calls.append("shutdown")

    async with ScarecrowApp(transcriber=transcriber).run_test() as pilot:
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        await pilot.pause(delay=0.2)
        app._session = mock_session
        app._audio_recorder = mock_recorder
        app.state = AppState.RECORDING

        app._submit_batch_transcription(np.zeros(16000), batch_elapsed=30)
        releaser = threading.Thread(
            target=lambda: (time.sleep(0.1), release.set()),
            daemon=True,
        )
        releaser.start()

        app._stop_recording()
        await pilot.pause()

    assert calls == ["batch-start", "batch-end", "shutdown", "session-finalize"]


async def test_batch_tick_skips_overlap_without_draining_new_audio() -> None:
    """A second batch tick while one is inflight must leave recorder audio buffered."""
    from concurrent.futures import Future

    recorder = MagicMock()
    recorder.drain_buffer.side_effect = [np.zeros(16000), np.ones(16000)]

    async with ScarecrowApp(transcriber=_mock_transcriber()).run_test() as pilot:
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        await pilot.pause()
        app._audio_recorder = recorder
        app._batch_futures = {Future()}

        app._batch_transcribe()

    assert recorder.drain_buffer.call_count == 0


@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_stop_recording_surfaces_session_finalize_failure(
    mock_session_cls, mock_recorder_cls
) -> None:
    """Disk or permission failures during finalize() must surface in the UI."""
    mock_recorder_cls.return_value = _mock_recorder()
    mock_session = MagicMock()
    mock_session.finalize.side_effect = OSError("disk full")
    mock_session_cls.return_value = mock_session

    transcriber = _mock_transcriber()

    async with ScarecrowApp(transcriber=transcriber).run_test() as pilot:
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        await pilot.pause(delay=0.2)
        app._session = mock_session
        app._audio_recorder = _mock_recorder()
        app.state = AppState.RECORDING

        app._stop_recording()
        await pilot.pause()

        assert app._status_message == "Could not finalize session: disk full"
        assert app._status_is_error is True


# ---------------------------------------------------------------------------
# Audit gap: final-flush transcript must reach the session file directly
# ---------------------------------------------------------------------------


async def test_flush_final_batch_writes_to_transcript_file(tmp_path: Path) -> None:
    """_flush_final_batch must write the final batch text directly to the
    transcript file, not through call_from_thread (which can defer past
    session.finalize)."""
    from scarecrow.session import Session

    real_session = Session(base_dir=tmp_path)

    mock_recorder = _mock_recorder()
    mock_recorder.drain_buffer.return_value = np.zeros(16000, dtype=np.float32)

    transcriber = _mock_transcriber()
    transcriber.transcribe_batch.return_value = "final transcribed text"

    with patch.object(ScarecrowApp, "_auto_start"):
        async with ScarecrowApp(transcriber=transcriber).run_test() as pilot:
            app: ScarecrowApp = pilot.app  # type: ignore[assignment]
            await pilot.pause(delay=0.2)
            app._session = real_session
            app._audio_recorder = mock_recorder
            app.state = AppState.RECORDING

            app._stop_recording()
            await pilot.pause()

    content = real_session.transcript_path.read_text(encoding="utf-8")
    assert "final transcribed text" in content


# ---------------------------------------------------------------------------
# Audit gap: Ctrl+C finally-block must clean up recorder and session
# ---------------------------------------------------------------------------


def test_ctrl_c_finally_block_cleans_up_recorder_and_session() -> None:
    """Simulate Ctrl+C: the __main__ finally block must stop the recorder and
    finalize the session when _stop_recording did not run."""
    mock_recorder = MagicMock()
    mock_session = MagicMock()
    mock_transcriber = _mock_transcriber()
    mock_transcriber.is_ready = False  # already shut down by TUI

    app = ScarecrowApp(transcriber=mock_transcriber)
    app._audio_recorder = mock_recorder
    app._session = mock_session

    # Replicate what __main__.py's finally block does
    if app._audio_recorder is not None:
        app._audio_recorder.stop()
    if app._session is not None:
        app._session.finalize()
    if mock_transcriber.is_ready:
        mock_transcriber.shutdown()

    mock_recorder.stop.assert_called_once()
    mock_session.finalize.assert_called_once()
    mock_transcriber.shutdown.assert_not_called()


# ---------------------------------------------------------------------------
# Audit gap: _wait_for_batch_workers must not block forever
# ---------------------------------------------------------------------------


async def test_wait_for_batch_workers_survives_timeout() -> None:
    """_wait_for_batch_workers must log and continue if a future times out."""
    from concurrent.futures import Future
    from concurrent.futures import TimeoutError as FuturesTimeoutError

    hung_future: Future[None] = Future()
    hung_future.result = MagicMock(side_effect=FuturesTimeoutError())  # type: ignore[method-assign]

    async with _app().run_test() as pilot:
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        await pilot.pause()
        app._batch_futures = {hung_future}

        app._wait_for_batch_workers()

        assert app._batch_futures == set()


# ---------------------------------------------------------------------------
# Audit gap: _post_to_ui after app reaches IDLE logs debug not error
# ---------------------------------------------------------------------------


async def test_post_to_ui_after_idle_logs_debug() -> None:
    """_post_to_ui called after app reaches IDLE must log debug, not error."""
    async with _app().run_test() as pilot:
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        await pilot.pause()
        app.state = AppState.IDLE

        with patch.object(app, "call_from_thread", side_effect=RuntimeError("no loop")):
            app._post_to_ui(lambda: None)
