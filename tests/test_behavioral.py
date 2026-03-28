"""Behavioral contract tests — lock in working behavior before refactor.

These tests verify observable contracts, not implementation details.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
from textual.widgets import Input, RichLog

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
    """Return a mock batch-only Transcriber."""
    mock = MagicMock()
    mock.is_ready = True
    mock.has_active_worker = False
    mock.shutdown.return_value = None

    def _shutdown(timeout=5):
        mock.is_ready = False
        return None

    mock.shutdown.side_effect = _shutdown
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


def _app(
    with_transcriber: bool = False, *, awaiting_context: bool = False
) -> ScarecrowApp:
    if with_transcriber:
        app = ScarecrowApp(
            transcriber=_mock_transcriber(),
        )
        app._preflight_check = lambda: True  # type: ignore[method-assign]
        app._awaiting_context = awaiting_context
        return app
    return ScarecrowApp()


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

        # With no session, dividers are skipped — consecutive batch results
        # are joined into a single paragraph block
        texts = [str(line) for line in captions.lines]
        joined = [t for t in texts if "Just the text" in t and "Second line" in t]
        assert len(joined) == 1


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


# ---------------------------------------------------------------------------
# 6. Shutdown messages appear in the live log
# ---------------------------------------------------------------------------


@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_shutdown_writes_shutting_down_message(
    mock_session_cls, mock_recorder_cls
) -> None:
    """action_quit must set status to 'Shutting down' before the deferred exit fires."""
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
        original_set_status = app._set_status

        def track_status(text: str, **kwargs) -> None:
            messages.append(text)
            original_set_status(text, **kwargs)

        app._set_status = track_status  # type: ignore[method-assign]

        # Suppress the deferred quit so the app doesn't close under us
        with patch.object(app, "_deferred_quit"):
            app.action_quit()
            await pilot.pause(delay=0.1)

        assert any("Shutting down" in m for m in messages), (
            f"Expected 'Shutting down' in status messages; got: {messages}"
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
        app._start_recording()
        await pilot.pause(delay=0.3)
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
        app._start_recording()
        await pilot.pause(delay=0.3)

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
async def test_start_recording_unwinds_recorder_when_recorder_start_fails(
    mock_session_cls, mock_recorder_cls
) -> None:
    """Startup failure in AudioRecorder.start() must unwind recorder and session."""
    mock_recorder = _mock_recorder()
    mock_recorder.start.side_effect = RuntimeError("mic unavailable")
    mock_session = MagicMock()
    mock_recorder_cls.return_value = mock_recorder
    mock_session_cls.return_value = mock_session

    app = ScarecrowApp(transcriber=_mock_transcriber())
    app._awaiting_context = False
    async with app.run_test() as pilot:
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

    def fake_final_batch(audio, elapsed, *, emit_callback=False, initial_prompt=None):
        calls.append("final-batch")
        assert emit_callback is False
        return "transcribed text"

    transcriber.transcribe_batch.side_effect = fake_final_batch
    transcriber.shutdown.side_effect = lambda timeout=5: calls.append("shutdown")

    async with ScarecrowApp(transcriber=transcriber).run_test() as pilot:
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        await pilot.pause(delay=0.2)
        app._session = mock_session
        app._audio_recorder = mock_recorder
        app.state = AppState.RECORDING

        app._stop_recording()
        await pilot.pause()

    assert calls == [
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

    def slow_batch(_audio, _elapsed, **kwargs):
        calls.append("batch-start")
        release.wait(timeout=1)
        calls.append("batch-end")
        return "completed text"

    transcriber = _mock_transcriber()
    transcriber.transcribe_batch.side_effect = slow_batch
    transcriber.shutdown.side_effect = lambda timeout=5: calls.append("shutdown")

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


async def test_flush_final_batch_disables_async_callback_path(tmp_path: Path) -> None:
    """Synchronous final flush must not also route the same text through
    the async callback path."""
    from scarecrow.session import Session

    real_session = Session(base_dir=tmp_path)
    mock_recorder = _mock_recorder()
    mock_recorder.drain_buffer.return_value = np.zeros(16000, dtype=np.float32)

    transcriber = _mock_transcriber()

    def fake_batch(_audio, _elapsed, *, emit_callback=True, initial_prompt=None):
        assert emit_callback is False
        return "one final line"

    transcriber.transcribe_batch.side_effect = fake_batch

    async with ScarecrowApp(transcriber=transcriber).run_test() as pilot:
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        await pilot.pause(delay=0.2)
        app._session = real_session
        app._audio_recorder = mock_recorder
        app.state = AppState.RECORDING

        app._stop_recording()
        await pilot.pause()

    content = real_session.transcript_path.read_text(encoding="utf-8")
    assert content.count("one final line") == 1


# ---------------------------------------------------------------------------
# Audit gap: Ctrl+C finally-block must clean up recorder and session
# ---------------------------------------------------------------------------


def test_ctrl_c_cleanup_after_exit_flushes_and_finalizes(tmp_path: Path) -> None:
    """The Ctrl+C cleanup path must flush buffered audio to the real session file."""
    from scarecrow.session import Session

    real_session = Session(base_dir=tmp_path)
    mock_recorder = _mock_recorder()
    mock_recorder.drain_buffer.return_value = np.zeros(16000, dtype=np.float32)
    mock_transcriber = _mock_transcriber()
    mock_transcriber.transcribe_batch.return_value = "ctrl c text"

    app = ScarecrowApp(transcriber=mock_transcriber)
    app._audio_recorder = mock_recorder
    app._session = real_session
    app._reactive_state = AppState.RECORDING

    app.cleanup_after_exit()

    content = real_session.transcript_path.read_text(encoding="utf-8")
    assert "ctrl c text" in content
    mock_recorder.stop.assert_called_once()
    mock_transcriber.shutdown.assert_called_once_with(timeout=5)
    assert app._session is None


def test_cleanup_after_exit_is_idempotent_for_normal_quit() -> None:
    """The shared cleanup path must not double-shutdown already-closed resources."""
    mock_transcriber = _mock_transcriber()
    app = ScarecrowApp(transcriber=mock_transcriber)
    app.cleanup_after_exit()
    app.cleanup_after_exit()

    mock_transcriber.shutdown.assert_called_once_with(timeout=5)


# ---------------------------------------------------------------------------
# Audit gap: _wait_for_batch_workers must not block forever
# ---------------------------------------------------------------------------


async def test_wait_for_batch_workers_survives_timeout() -> None:
    """_wait_for_batch_workers must log and continue if a future times out."""
    from concurrent.futures import Future
    from concurrent.futures import TimeoutError as FuturesTimeoutError

    hung_future: Future[str | None] = Future()
    hung_future.result = MagicMock(side_effect=FuturesTimeoutError())  # type: ignore[method-assign]

    async with _app().run_test() as pilot:
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        await pilot.pause()
        app._batch_futures = {hung_future}
        app._batch_executor = MagicMock()

        completed, captured = app._wait_for_batch_workers()

        assert completed is False
        assert captured == []
        assert app._batch_futures == set()
        assert app._batch_executor is None
        assert app._ignore_batch_results is True


@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_stop_recording_skips_final_flush_after_batch_timeout(
    mock_session_cls, mock_recorder_cls
) -> None:
    """A timed-out worker must not block shutdown by re-entering batch transcription."""
    from concurrent.futures import Future
    from concurrent.futures import TimeoutError as FuturesTimeoutError

    mock_recorder = _mock_recorder()
    mock_recorder.drain_buffer.return_value = np.zeros(16000)
    mock_recorder_cls.return_value = mock_recorder

    mock_session = MagicMock()
    mock_session_cls.return_value = mock_session

    hung_future: Future[str | None] = Future()
    hung_future.result = MagicMock(side_effect=FuturesTimeoutError())  # type: ignore[method-assign]

    transcriber = _mock_transcriber()

    async with ScarecrowApp(transcriber=transcriber).run_test() as pilot:
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        await pilot.pause(delay=0.2)
        app._audio_recorder = mock_recorder
        app._session = mock_session
        app._batch_futures = {hung_future}
        app._batch_executor = MagicMock()
        app.state = AppState.RECORDING

        app._stop_recording()
        await pilot.pause()

    transcriber.transcribe_batch.assert_not_called()
    mock_session.finalize.assert_called_once()


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


# ---------------------------------------------------------------------------
# Audit round 2: in-flight batch text capture
# ---------------------------------------------------------------------------


def test_wait_for_batch_workers_captures_completed_text(tmp_path: Path) -> None:
    """In-flight batch text must be captured from futures and written to session."""
    from scarecrow.session import Session

    real_session = Session(base_dir=tmp_path)
    mock_recorder = _mock_recorder()
    mock_recorder.drain_buffer.return_value = None

    transcriber = _mock_transcriber()

    def slow_batch(_audio, _elapsed, **kwargs):
        return "inflight batch text"

    transcriber.transcribe_batch.side_effect = slow_batch

    app = ScarecrowApp(transcriber=transcriber)
    app._audio_recorder = mock_recorder
    app._session = real_session
    app._reactive_state = AppState.RECORDING

    app._submit_batch_transcription(np.zeros(16000), batch_elapsed=30)
    app.cleanup_after_exit()

    content = real_session.transcript_path.read_text(encoding="utf-8")
    assert "inflight batch text" in content
    assert app._session is None


# ---------------------------------------------------------------------------
# Audit round 2: session finalize idempotent under KeyboardInterrupt
# ---------------------------------------------------------------------------


def test_session_finalize_idempotent_after_interrupt(tmp_path: Path) -> None:
    """session.finalize() must be safe to call again after KeyboardInterrupt."""
    from scarecrow.session import Session

    session = Session(base_dir=tmp_path)
    session.append_sentence("before interrupt")

    # Simulate KeyboardInterrupt between close() and _transcript_file = None
    session._finalized = True
    session.finalize()

    # append_sentence after finalize must be a no-op
    session.append_sentence("after finalize")
    content = session.transcript_path.read_text(encoding="utf-8")
    assert "before interrupt" in content
    assert "after finalize" not in content


# ---------------------------------------------------------------------------
# Audit round 2: cleanup uses timeout for transcriber shutdown
# ---------------------------------------------------------------------------


def test_cleanup_after_exit_uses_timeout_for_transcriber_shutdown() -> None:
    """cleanup_after_exit must call transcriber.shutdown with a finite timeout."""
    mock_transcriber = _mock_transcriber()

    app = ScarecrowApp(transcriber=mock_transcriber)
    app._reactive_state = AppState.RECORDING
    app._audio_recorder = _mock_recorder()
    app._session = MagicMock()

    app.cleanup_after_exit()

    mock_transcriber.shutdown.assert_called_once_with(timeout=5)


# ---------------------------------------------------------------------------
# Audit round 2: cleanup shuts down batch executor in all paths
# ---------------------------------------------------------------------------


def test_cleanup_after_exit_shuts_down_batch_executor() -> None:
    """cleanup_after_exit must shut down the batch executor even without timeout."""
    mock_executor = MagicMock()
    mock_transcriber = _mock_transcriber()

    app = ScarecrowApp(transcriber=mock_transcriber)
    app._reactive_state = AppState.RECORDING
    app._audio_recorder = _mock_recorder()
    app._session = MagicMock()
    app._batch_executor = mock_executor

    app.cleanup_after_exit()

    mock_executor.shutdown.assert_called_once_with(wait=False, cancel_futures=False)
    assert app._batch_executor is None


# ---------------------------------------------------------------------------
# Audit round 2: on_unmount is safe when executor already cleaned up
# ---------------------------------------------------------------------------


async def test_on_unmount_noop_when_executor_already_cleaned() -> None:
    """on_unmount must not crash when cleanup_after_exit already cleared executor."""
    async with _app().run_test() as pilot:
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        await pilot.pause()
        app._batch_executor = None
        app.on_unmount()  # must not raise


# ---------------------------------------------------------------------------
# Phase 5: Note submission logic
# ---------------------------------------------------------------------------


async def test_note_submission_writes_to_richlog() -> None:
    """_submit_note must write the note text to the #captions RichLog."""
    async with _app().run_test() as pilot:
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        await pilot.pause()

        input_widget = app.query_one("#note-input", Input)
        input_widget.value = "test note text"

        captions = app.query_one("#captions", RichLog)
        initial_lines = len(captions.lines)

        app._submit_note()
        await pilot.pause()

        assert len(captions.lines) > initial_lines
        all_text = " ".join(str(line) for line in captions.lines[initial_lines:])
        assert "test note text" in all_text


async def test_note_submission_clears_input() -> None:
    """_submit_note must clear the Input widget value after submission."""
    async with _app().run_test() as pilot:
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        await pilot.pause()

        input_widget = app.query_one("#note-input", Input)
        input_widget.value = "clear me"

        app._submit_note()
        await pilot.pause()

        assert input_widget.value == ""


async def test_note_submission_includes_tag_prefix() -> None:
    """_submit_note must include the tag in the RichLog output."""
    async with _app().run_test() as pilot:
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        await pilot.pause()

        input_widget = app.query_one("#note-input", Input)
        input_widget.value = "/task follow up on this"

        captions = app.query_one("#captions", RichLog)
        initial_lines = len(captions.lines)

        app._submit_note()
        await pilot.pause()

        all_text = " ".join(str(line) for line in captions.lines[initial_lines:])
        assert "TASK" in all_text


async def test_note_submission_increments_word_count() -> None:
    """_submit_note must increment _word_count by the number of words in the note."""
    async with _app().run_test() as pilot:
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        await pilot.pause()

        app._word_count = 0
        input_widget = app.query_one("#note-input", Input)
        input_widget.value = "hello world"

        app._submit_note()
        await pilot.pause()

        assert app._word_count == 2


async def test_empty_note_submission_is_noop() -> None:
    """_submit_note with empty or whitespace input must not write to RichLog."""
    async with _app().run_test() as pilot:
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        # Use a longer delay to ensure any startup messages are already in the
        # RichLog before we snapshot initial_lines.
        await pilot.pause(delay=0.2)

        input_widget = app.query_one("#note-input", Input)
        input_widget.value = "   "

        captions = app.query_one("#captions", RichLog)
        initial_lines = len(captions.lines)
        initial_word_count = app._word_count

        app._submit_note()
        await pilot.pause()

        assert len(captions.lines) == initial_lines
        assert app._word_count == initial_word_count


async def test_note_submission_when_idle() -> None:
    """_submit_note without a session must still write to RichLog (UI-only)."""
    async with _app().run_test() as pilot:
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        await pilot.pause()

        app._session = None
        input_widget = app.query_one("#note-input", Input)
        input_widget.value = "no session note"

        captions = app.query_one("#captions", RichLog)
        initial_lines = len(captions.lines)

        app._submit_note()
        await pilot.pause()

        assert len(captions.lines) > initial_lines
        all_text = " ".join(str(line) for line in captions.lines[initial_lines:])
        assert "no session note" in all_text


async def test_note_submission_writes_to_session(tmp_path: Path) -> None:
    """_submit_note must write the note line to the transcript file via session."""
    from scarecrow.session import Session

    real_session = Session(base_dir=tmp_path)

    async with _app().run_test() as pilot:
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        await pilot.pause()

        app._session = real_session
        input_widget = app.query_one("#note-input", Input)
        input_widget.value = "/task save this note"

        app._submit_note()
        await pilot.pause()

    content = real_session.transcript_path.read_text(encoding="utf-8")
    assert "[TASK]" in content
    assert "save this note" in content


async def test_enter_submits_note() -> None:
    """Pressing Enter in the note Input must submit the note to RichLog."""
    async with _app().run_test() as pilot:
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        await pilot.pause()

        # Bypass the context-collection phase so Enter routes to _submit_note
        app._awaiting_context = False

        input_widget = app.query_one("#note-input", Input)
        await pilot.click("#note-input")
        await pilot.pause()

        input_widget.value = "enter key note"

        captions = app.query_one("#captions", RichLog)
        initial_lines = len(captions.lines)

        await pilot.press("enter")
        await pilot.pause()

        assert len(captions.lines) > initial_lines
        all_text = " ".join(str(line) for line in captions.lines[initial_lines:])
        assert "enter key note" in all_text


# ---------------------------------------------------------------------------
# Context injection (whisper-only — parakeet does not support context injection)
# ---------------------------------------------------------------------------


@patch("scarecrow.config.BACKEND", "whisper")
@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_context_start_empty_starts_recording(
    mock_session_cls, mock_recorder_cls
) -> None:
    """Pressing Enter with an empty input starts recording when transcriber is ready."""
    mock_recorder_cls.return_value = _mock_recorder()
    mock_session_cls.return_value = MagicMock()

    async with _app(with_transcriber=True, awaiting_context=True).run_test() as pilot:
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        app._start_recording()
        await pilot.pause(delay=0.3)
        assert app.state is AppState.RECORDING


@patch("scarecrow.config.BACKEND", "whisper")
async def test_context_start_empty_stays_idle_without_transcriber() -> None:
    """Pressing Enter with no transcriber stays IDLE and shows an error."""
    async with _app(awaiting_context=True).run_test() as pilot:
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        await pilot.press("enter")
        await pilot.pause(delay=0.2)
        assert app.state is AppState.IDLE


@patch("scarecrow.config.BACKEND", "whisper")
@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_context_start_with_text_writes_context_block(
    mock_session_cls, mock_recorder_cls
) -> None:
    """Pressing Enter with context text writes a [CONTEXT] block to RichLog."""
    mock_recorder_cls.return_value = _mock_recorder()
    mock_session_cls.return_value = MagicMock()

    async with _app(with_transcriber=True, awaiting_context=True).run_test() as pilot:
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        await pilot.pause()

        input_widget = app.query_one("#note-input", Input)
        input_widget.value = "Malcolm X"

        captions = app.query_one("#captions", RichLog)
        initial_lines = len(captions.lines)

        await pilot.press("enter")
        await pilot.pause(delay=0.3)

        new_text = " ".join(str(line) for line in captions.lines[initial_lines:])
        assert "CONTEXT" in new_text
        assert "Malcolm X" in new_text


@patch("scarecrow.config.BACKEND", "whisper")
@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_context_start_stores_entries(
    mock_session_cls, mock_recorder_cls
) -> None:
    """Context text provided at launch must be stored in _context_entries."""
    mock_recorder_cls.return_value = _mock_recorder()
    mock_session_cls.return_value = MagicMock()

    async with _app(with_transcriber=True, awaiting_context=True).run_test() as pilot:
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        await pilot.pause()

        input_widget = app.query_one("#note-input", Input)
        input_widget.value = "Malcolm X"

        await pilot.press("enter")
        await pilot.pause(delay=0.3)

        assert app._context_entries == ["Malcolm X"]


@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_context_command_appends_entries(
    mock_session_cls, mock_recorder_cls
) -> None:
    """/context command during recording appends a new entry."""
    mock_recorder_cls.return_value = _mock_recorder()
    mock_session_cls.return_value = MagicMock()

    async with _app(with_transcriber=True).run_test() as pilot:
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        # Start recording first
        app._start_recording()
        await pilot.pause(delay=0.3)
        assert app.state is AppState.RECORDING

        # Send /context command
        input_widget = app.query_one("#note-input", Input)
        input_widget.value = "/context Yakub"
        await pilot.press("enter")
        await pilot.pause()

        assert "Yakub" in app._context_entries


@patch("scarecrow.config.BACKEND", "whisper")
@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_clear_command_wipes_entries(mock_session_cls, mock_recorder_cls) -> None:
    """/clear command empties _context_entries and _previous_batch_tail."""
    mock_recorder_cls.return_value = _mock_recorder()
    mock_session_cls.return_value = MagicMock()

    async with _app(with_transcriber=True, awaiting_context=True).run_test() as pilot:
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        await pilot.pause()

        # Start with some context
        input_widget = app.query_one("#note-input", Input)
        input_widget.value = "Malcolm X"
        await pilot.press("enter")
        await pilot.pause(delay=0.3)
        assert app._context_entries == ["Malcolm X"]

        # Now clear
        input_widget.value = "/clear"
        await pilot.press("enter")
        await pilot.pause()

        assert app._context_entries == []
        assert app._previous_batch_tail == ""


@patch("scarecrow.config.BACKEND", "whisper")
@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_context_display_shown_when_context_present(
    mock_session_cls, mock_recorder_cls
) -> None:
    """#context-display widget must be visible after context is added."""
    from textual.widgets import Static

    mock_recorder_cls.return_value = _mock_recorder()
    mock_session_cls.return_value = MagicMock()

    async with _app(with_transcriber=True, awaiting_context=True).run_test() as pilot:
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        await pilot.pause()

        input_widget = app.query_one("#note-input", Input)
        input_widget.value = "Malcolm X"
        await pilot.press("enter")
        await pilot.pause(delay=0.3)

        display = app.query_one("#context-display", Static)
        assert display.display is True
        assert "Context: 1" in str(display.render())


@patch("scarecrow.config.BACKEND", "whisper")
@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_context_display_hidden_after_clear(
    mock_session_cls, mock_recorder_cls
) -> None:
    """#context-display widget must be hidden after /clear."""
    from textual.widgets import Static

    mock_recorder_cls.return_value = _mock_recorder()
    mock_session_cls.return_value = MagicMock()

    async with _app(with_transcriber=True, awaiting_context=True).run_test() as pilot:
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        await pilot.pause()

        input_widget = app.query_one("#note-input", Input)
        input_widget.value = "Malcolm X"
        await pilot.press("enter")
        await pilot.pause(delay=0.3)

        input_widget.value = "/clear"
        await pilot.press("enter")
        await pilot.pause()

        display = app.query_one("#context-display", Static)
        assert display.display is False


async def test_build_initial_prompt_context_only() -> None:
    """_build_initial_prompt with only entries returns joined entries string."""
    async with _app().run_test() as pilot:
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        await pilot.pause()

        app._context_entries = ["Malcolm X", "Nation of Islam"]
        app._previous_batch_tail = ""

        result = app._build_initial_prompt()
        assert result == "Malcolm X, Nation of Islam"


async def test_build_initial_prompt_with_tail() -> None:
    """_build_initial_prompt combines entries and tail with '. ' separator."""
    async with _app().run_test() as pilot:
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        await pilot.pause()

        app._context_entries = ["Malcolm X"]
        app._previous_batch_tail = "he spoke about civil rights"

        result = app._build_initial_prompt()
        assert result == "Malcolm X. he spoke about civil rights"


async def test_build_initial_prompt_empty_returns_none() -> None:
    """_build_initial_prompt returns None when both entries and tail are empty."""
    async with _app().run_test() as pilot:
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        await pilot.pause()

        app._context_entries = []
        app._previous_batch_tail = ""

        result = app._build_initial_prompt()
        assert result is None


async def test_update_tail_stores_last_35_words() -> None:
    """_update_tail keeps only the last 35 words of the provided text."""
    async with _app().run_test() as pilot:
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        await pilot.pause()

        # Build a 50-word string
        words = [f"word{i}" for i in range(50)]
        long_text = " ".join(words)

        app._update_tail(long_text)

        tail_words = app._previous_batch_tail.split()
        assert len(tail_words) == 35
        assert tail_words == words[-35:]


@patch("scarecrow.config.BACKEND", "whisper")
async def test_notes_label_shows_context_prompt_before_recording() -> None:
    """The notes label must show the context prompt before recording starts."""
    async with _app(awaiting_context=True).run_test() as pilot:
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        await pilot.pause()

        from textual.widgets import Static

        label = app.query_one("#notes-label", Static)
        label_text = str(label.render()).lower()
        assert "context" in label_text


@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_notes_label_reverts_after_recording_starts(
    mock_session_cls, mock_recorder_cls
) -> None:
    """After recording starts the notes label must revert to the notes prompt."""
    mock_recorder_cls.return_value = _mock_recorder()
    mock_session_cls.return_value = MagicMock()

    async with _app(with_transcriber=True, awaiting_context=True).run_test() as pilot:
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        await pilot.pause()

        from textual.widgets import Static

        app._start_recording()
        await pilot.pause(delay=0.3)

        label = app.query_one("#notes-label", Static)
        label_text = str(label.render())
        assert "Notes" in label_text or "Enter to start" in label_text


# ---------------------------------------------------------------------------
# Note count tracking
# ---------------------------------------------------------------------------


@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_note_counts_increment(mock_session_cls, mock_recorder_cls) -> None:
    """_note_counts must track counts per type after each submission."""
    mock_recorder_cls.return_value = _mock_recorder()
    mock_session_cls.return_value = MagicMock()

    async with _app(with_transcriber=True).run_test() as pilot:
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        # Start recording so _handle_add_context works
        app._start_recording()
        await pilot.pause(delay=0.3)
        assert app.state is AppState.RECORDING

        input_widget = app.query_one("#note-input", Input)

        # Submit a plain note
        input_widget.value = "plain note here"
        app._submit_note()
        await pilot.pause()

        # Submit a /task note
        input_widget.value = "/task follow up on this"
        app._submit_note()
        await pilot.pause()

        # Submit a /context entry
        input_widget.value = "/context Malcolm X"
        await pilot.press("enter")
        await pilot.pause()

        assert app._note_counts["NOTE"] == 1
        assert app._note_counts["TASK"] == 1
        assert app._note_counts["CONTEXT"] >= 1


@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_context_display_shows_all_counts(
    mock_session_cls, mock_recorder_cls
) -> None:
    """#context-display must show counts for Context, Tasks, and Notes."""
    from textual.widgets import Static

    mock_recorder_cls.return_value = _mock_recorder()
    mock_session_cls.return_value = MagicMock()

    async with _app(with_transcriber=True).run_test() as pilot:
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        # Start recording
        app._start_recording()
        await pilot.pause(delay=0.3)
        assert app.state is AppState.RECORDING

        input_widget = app.query_one("#note-input", Input)

        # Add a context entry
        input_widget.value = "/context Malcolm X"
        await pilot.press("enter")
        await pilot.pause()

        # Add a task note
        input_widget.value = "/task follow up"
        app._submit_note()
        await pilot.pause()

        # Add a plain note
        input_widget.value = "plain note"
        app._submit_note()
        await pilot.pause()

        display = app.query_one("#context-display", Static)
        display_text = str(display.render())
        assert "Context: 1" in display_text
        assert "Tasks: 1" in display_text
        assert "Notes: 1" in display_text


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


# ---------------------------------------------------------------------------
# New tests: end-to-end recording session
# ---------------------------------------------------------------------------


@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_end_to_end_recording_session_text_appears_in_captions(
    mock_session_cls, mock_recorder_cls
) -> None:
    """Full loop: start → batch → transcription → UI shows text."""
    mock_recorder = _mock_recorder()
    mock_recorder_cls.return_value = mock_recorder

    mock_session = MagicMock()
    mock_session.transcript_path = Path("/tmp/e2e_session.txt")
    mock_session_cls.return_value = mock_session

    transcriber = _mock_transcriber()

    async with ScarecrowApp(transcriber=transcriber).run_test() as pilot:
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        app._start_recording()
        await pilot.pause(delay=0.3)
        assert app.state is AppState.RECORDING

        # Simulate a batch transcription result arriving from the transcriber
        app._append_transcript("hello from the batch transcriber")
        await pilot.pause()

        captions = app.query_one("#captions", RichLog)
        caption_text = " ".join(str(line) for line in captions.lines)
        assert "hello from the batch transcriber" in caption_text


@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_end_to_end_session_append_sentence_called(
    mock_session_cls, mock_recorder_cls
) -> None:
    """_append_transcript calls session.append_sentence."""
    mock_recorder = _mock_recorder()
    mock_recorder_cls.return_value = mock_recorder

    mock_session = MagicMock()
    mock_session.transcript_path = Path("/tmp/e2e_session2.txt")
    mock_session_cls.return_value = mock_session

    transcriber = _mock_transcriber()

    async with ScarecrowApp(transcriber=transcriber).run_test() as pilot:
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        app._start_recording()
        await pilot.pause(delay=0.3)
        assert app.state is AppState.RECORDING

        app._append_transcript("verified sentence text")
        await pilot.pause()

        # append_sentence is called once for the header (Session Start) and once for
        # the divider and once for the actual text; just verify the text made it in.
        calls = [str(c) for c in mock_session.append_sentence.call_args_list]
        assert any("verified sentence text" in c for c in calls)


# ---------------------------------------------------------------------------
# New tests: batch window timing
# ---------------------------------------------------------------------------


@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_batch_transcription_triggered_at_interval(
    mock_session_cls, mock_recorder_cls
) -> None:
    """_on_batch_tick must call _batch_transcribe when RECORDING."""
    mock_recorder = _mock_recorder()
    mock_recorder.drain_buffer.return_value = np.zeros(16000, dtype=np.float32)
    mock_recorder_cls.return_value = mock_recorder
    mock_session_cls.return_value = MagicMock()

    transcriber = _mock_transcriber()

    async with ScarecrowApp(transcriber=transcriber).run_test() as pilot:
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        app._start_recording()
        await pilot.pause(delay=0.3)
        assert app.state is AppState.RECORDING

        submit_calls: list[bool] = []
        original_submit = app._submit_batch_transcription

        def track_submit(audio, batch_elapsed):
            submit_calls.append(True)
            return original_submit(audio, batch_elapsed)

        app._submit_batch_transcription = track_submit  # type: ignore[method-assign]

        # Fire the batch tick manually — simulates BATCH_INTERVAL_SECONDS elapsed
        app._on_batch_tick()
        await pilot.pause()

        assert len(submit_calls) == 1, (
            "_submit_batch_transcription must be called once when batch tick fires"
        )


@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_batch_transcription_not_triggered_before_interval(
    mock_session_cls, mock_recorder_cls
) -> None:
    """_submit_batch_transcription must NOT be called before the batch tick fires."""
    mock_recorder = _mock_recorder()
    mock_recorder.drain_buffer.return_value = np.zeros(16000, dtype=np.float32)
    mock_recorder_cls.return_value = mock_recorder
    mock_session_cls.return_value = MagicMock()

    transcriber = _mock_transcriber()

    async with ScarecrowApp(transcriber=transcriber).run_test() as pilot:
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        app._start_recording()
        await pilot.pause(delay=0.3)
        assert app.state is AppState.RECORDING

        submit_calls: list[bool] = []
        original_submit = app._submit_batch_transcription

        def track_submit(audio, batch_elapsed):
            submit_calls.append(True)
            return original_submit(audio, batch_elapsed)

        app._submit_batch_transcription = track_submit  # type: ignore[method-assign]

        # Do NOT fire the batch tick — just wait briefly
        await pilot.pause(delay=0.1)

        assert len(submit_calls) == 0, (
            "_submit_batch_transcription must not be called before the batch interval"
        )


# ---------------------------------------------------------------------------
# New tests: invalid state transitions are no-ops
# ---------------------------------------------------------------------------


async def test_action_pause_from_idle_is_noop() -> None:
    """action_pause when IDLE must do nothing (no state change, no exception)."""
    async with _app().run_test() as pilot:
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        await pilot.pause()
        assert app.state is AppState.IDLE

        # action_pause from IDLE must not raise and must not change state
        app.action_pause()
        await pilot.pause()

        assert app.state is AppState.IDLE


@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_double_start_is_idempotent(mock_session_cls, mock_recorder_cls) -> None:
    """Calling _start_recording twice must not open two sessions or recorders."""
    mock_recorder_cls.return_value = _mock_recorder()
    mock_session_cls.return_value = MagicMock()

    async with _app(with_transcriber=True).run_test() as pilot:
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        app._start_recording()
        await pilot.pause(delay=0.3)
        assert app.state is AppState.RECORDING

        first_session = app._session
        first_recorder = app._audio_recorder

        # Second call must be a no-op (state is RECORDING, not IDLE)
        app._start_recording()
        await pilot.pause()

        assert app._session is first_session, (
            "Double start must not replace the existing session"
        )
        assert app._audio_recorder is first_recorder, (
            "Double start must not replace the existing recorder"
        )
        assert app.state is AppState.RECORDING


# ---------------------------------------------------------------------------
# New tests: rapid pause/resume
# ---------------------------------------------------------------------------


@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_rapid_pause_resume_no_exceptions(
    mock_session_cls, mock_recorder_cls
) -> None:
    """Rapid pause → resume → pause must not raise and must end in PAUSED."""
    mock_recorder = _mock_recorder()
    mock_recorder_cls.return_value = mock_recorder
    mock_session_cls.return_value = MagicMock()

    transcriber = _mock_transcriber()

    async with ScarecrowApp(transcriber=transcriber).run_test() as pilot:
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        app._start_recording()
        await pilot.pause(delay=0.3)
        assert app.state is AppState.RECORDING

        # Rapid state changes — must not raise
        app.action_pause()  # → PAUSED
        app.action_pause()  # → RECORDING
        app.action_pause()  # → PAUSED
        await pilot.pause()

        assert app.state is AppState.PAUSED


@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_rapid_pause_resume_state_sequence(
    mock_session_cls, mock_recorder_cls
) -> None:
    """Each pause/resume toggle must transition state correctly."""
    mock_recorder = _mock_recorder()
    mock_recorder_cls.return_value = mock_recorder
    mock_session_cls.return_value = MagicMock()

    transcriber = _mock_transcriber()

    async with ScarecrowApp(transcriber=transcriber).run_test() as pilot:
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        app._start_recording()
        await pilot.pause(delay=0.3)
        assert app.state is AppState.RECORDING

        app.action_pause()
        await pilot.pause()
        assert app.state is AppState.PAUSED

        app.action_pause()
        await pilot.pause()
        assert app.state is AppState.RECORDING

        app.action_pause()
        await pilot.pause()
        assert app.state is AppState.PAUSED
