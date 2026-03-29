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


def _read_jsonl(path: Path) -> list[dict]:
    import json

    lines = path.read_text(encoding="utf-8").strip().splitlines()
    return [json.loads(line) for line in lines]


def _mock_transcriber():
    """Return a mock batch-only Transcriber."""
    mock = MagicMock()
    mock.is_ready = True
    mock.consecutive_failures = 0
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


def _app(with_transcriber: bool = False) -> ScarecrowApp:
    if with_transcriber:
        app = ScarecrowApp(
            transcriber=_mock_transcriber(),
        )
        app._preflight_check = lambda: True  # type: ignore[method-assign]
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
        mock_session.transcript_path = Path("/tmp/test_session.jsonl")
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
        assert "test_session.jsonl" in new_lines_text, "Divider must contain path"


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
    mock_session.transcript_path = Path("/tmp/test-session/transcript.jsonl")
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
# 9. peak_level property exists on AudioRecorder and starts at 0.0
# ---------------------------------------------------------------------------


def test_audio_recorder_peak_level_property_exists(tmp_path: Path) -> None:
    """AudioRecorder must expose a peak_level property."""
    from scarecrow.recorder import AudioRecorder

    with patch.dict(
        "sys.modules", {"sounddevice": MagicMock(), "soundfile": MagicMock()}
    ):
        recorder = AudioRecorder(tmp_path / "audio.wav")
        assert hasattr(recorder, "peak_level")


def test_audio_recorder_peak_level_starts_at_zero(tmp_path: Path) -> None:
    """AudioRecorder.peak_level must be 0.0 before any audio is received."""
    from scarecrow.recorder import AudioRecorder

    with patch.dict(
        "sys.modules", {"sounddevice": MagicMock(), "soundfile": MagicMock()}
    ):
        recorder = AudioRecorder(tmp_path / "audio.wav")
        assert recorder.peak_level == 0.0


def test_audio_recorder_peak_level_is_float(tmp_path: Path) -> None:
    """AudioRecorder.peak_level must be a float, not an int or None."""
    from scarecrow.recorder import AudioRecorder

    with patch.dict(
        "sys.modules", {"sounddevice": MagicMock(), "soundfile": MagicMock()}
    ):
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
    app._preflight_check = lambda: False  # type: ignore[method-assign]
    async with app.run_test() as pilot:
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        # Re-enable so the manual call works
        app._preflight_check = lambda: True  # type: ignore[method-assign]
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

    def fake_final_batch(audio, elapsed, *, emit_callback=False, max_retries=None):
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

    events = _read_jsonl(real_session.transcript_path)
    texts = [e.get("text", "") for e in events if e.get("type") == "transcript"]
    assert any("final transcribed text" in t for t in texts)


async def test_flush_final_batch_disables_async_callback_path(tmp_path: Path) -> None:
    """Synchronous final flush must not also route the same text through
    the async callback path."""
    from scarecrow.session import Session

    real_session = Session(base_dir=tmp_path)
    mock_recorder = _mock_recorder()
    mock_recorder.drain_buffer.return_value = np.zeros(16000, dtype=np.float32)

    transcriber = _mock_transcriber()

    def fake_batch(_audio, _elapsed, *, emit_callback=True, max_retries=None):
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

    events = _read_jsonl(real_session.transcript_path)
    texts = [e.get("text", "") for e in events if e.get("type") == "transcript"]
    assert texts.count("one final line") == 1


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

    events = _read_jsonl(real_session.transcript_path)
    texts = [e.get("text", "") for e in events if e.get("type") == "transcript"]
    assert any("ctrl c text" in t for t in texts)
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

    events = _read_jsonl(real_session.transcript_path)
    texts = [e.get("text", "") for e in events if e.get("type") == "transcript"]
    assert any("inflight batch text" in t for t in texts)
    assert app._session is None


# ---------------------------------------------------------------------------
# Audit round 2: session finalize idempotent under KeyboardInterrupt
# ---------------------------------------------------------------------------


def test_session_finalize_idempotent_after_interrupt(tmp_path: Path) -> None:
    """session.finalize() must be safe to call again after KeyboardInterrupt."""
    from scarecrow.session import Session

    session = Session(base_dir=tmp_path)
    session.append_event({"type": "transcript", "text": "before interrupt"})

    # Simulate KeyboardInterrupt between close() and _transcript_file = None
    session._finalized = True
    session.finalize()

    # append_event after finalize must be a no-op
    session.append_event({"type": "transcript", "text": "after finalize"})
    content = session.transcript_path.read_text(encoding="utf-8")
    assert "before interrupt" in content
    assert "after finalize" not in content


# ---------------------------------------------------------------------------
# H1: KeyboardInterrupt during batch flush must not skip session finalization
# ---------------------------------------------------------------------------


def test_cleanup_handles_keyboard_interrupt_during_batch_wait(tmp_path: Path) -> None:
    """KeyboardInterrupt during _wait_for_batch_workers must still finalize session."""
    from scarecrow.session import Session

    real_session = Session(base_dir=tmp_path)
    mock_recorder = _mock_recorder()
    mock_transcriber = _mock_transcriber()

    app = ScarecrowApp(transcriber=mock_transcriber)
    app._audio_recorder = mock_recorder
    app._session = real_session
    app._reactive_state = AppState.RECORDING

    with patch.object(app, "_wait_for_batch_workers", side_effect=KeyboardInterrupt):
        app.cleanup_after_exit()

    # Session must be finalized (set to None) despite the KeyboardInterrupt
    assert app._session is None
    assert real_session.transcript_path.exists()


# ---------------------------------------------------------------------------
# H5: _flush_final_batch must pass max_retries=0 to skip retries during shutdown
# ---------------------------------------------------------------------------


def test_flush_final_batch_skips_retries(tmp_path: Path) -> None:
    """_flush_final_batch must call transcribe_batch with max_retries=0."""
    mock_recorder = _mock_recorder()
    mock_recorder.drain_buffer.return_value = np.zeros(16000, dtype=np.float32)
    mock_transcriber = _mock_transcriber()
    mock_transcriber.transcribe_batch.return_value = "final text"

    app = ScarecrowApp(transcriber=mock_transcriber)
    app._audio_recorder = mock_recorder
    app._session = None

    app._flush_final_batch(include_ui=False)

    call_kwargs = mock_transcriber.transcribe_batch.call_args
    assert call_kwargs.kwargs.get("max_retries") == 0, (
        "Final flush must pass max_retries=0 to skip retries during shutdown"
    )


# ---------------------------------------------------------------------------
# H6: Circuit breaker stops batch submission after repeated failures
# ---------------------------------------------------------------------------


def test_circuit_breaker_stops_batch_submission() -> None:
    """_submit_batch_transcription must return False after repeated failures."""
    mock_transcriber = _mock_transcriber()
    mock_transcriber.consecutive_failures = 3  # at or above threshold

    app = ScarecrowApp(transcriber=mock_transcriber)
    app._reactive_state = AppState.RECORDING

    warned: list[str] = []
    app._warn_transcript = lambda msg: warned.append(msg)  # type: ignore[method-assign]
    app._set_status = lambda msg, **kw: None  # type: ignore[method-assign]

    audio = np.zeros(16000, dtype=np.float32)
    result = app._submit_batch_transcription(audio, batch_elapsed=0)

    assert result is False
    assert app._circuit_breaker_shown is True
    assert len(warned) == 1
    assert "Transcription unavailable" in warned[0]


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
# RichLog pruning — UI must never hold more than RICHLOG_MAX_LINES
# ---------------------------------------------------------------------------


async def test_richlog_pruned_to_max_lines() -> None:
    """RichLog must not exceed RICHLOG_MAX_LINES after transcript writes."""
    from scarecrow.app import RICHLOG_MAX_LINES

    async with _app().run_test() as pilot:
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        await pilot.pause()

        app._session = None
        captions = app.query_one("#captions", RichLog)

        # Write more than RICHLOG_MAX_LINES entries
        for i in range(RICHLOG_MAX_LINES + 100):
            app._append_transcript(f"line {i}")
        await pilot.pause()

        assert len(captions.lines) <= RICHLOG_MAX_LINES


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

    events = _read_jsonl(real_session.transcript_path)
    note_events = [e for e in events if e.get("type") == "note"]
    assert any(
        e.get("tag") == "TASK" and "save this note" in e.get("text", "")
        for e in note_events
    )


async def test_enter_submits_note() -> None:
    """Pressing Enter in the note Input must submit the note to RichLog."""
    async with _app().run_test() as pilot:
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        await pilot.pause()

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

        assert app._note_counts["NOTE"] == 1
        assert app._note_counts["TASK"] == 1


@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_note_display_shows_task_and_note_counts(
    mock_session_cls, mock_recorder_cls
) -> None:
    """_note_counts must track Tasks and Notes counts after submissions."""
    mock_recorder_cls.return_value = _mock_recorder()
    mock_session_cls.return_value = MagicMock()

    async with _app(with_transcriber=True).run_test() as pilot:
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        app._start_recording()
        await pilot.pause(delay=0.3)
        assert app.state is AppState.RECORDING

        input_widget = app.query_one("#note-input", Input)

        # Add a task note
        input_widget.value = "/task follow up"
        app._submit_note()
        await pilot.pause()

        # Add a plain note
        input_widget.value = "plain note"
        app._submit_note()
        await pilot.pause()

        assert app._note_counts["TASK"] == 1
        assert app._note_counts["NOTE"] == 1


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
    mock_session.transcript_path = Path("/tmp/e2e_session.jsonl")
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
async def test_end_to_end_session_append_event_called(
    mock_session_cls, mock_recorder_cls
) -> None:
    """_append_transcript calls session.append_event."""
    mock_recorder = _mock_recorder()
    mock_recorder_cls.return_value = mock_recorder

    mock_session = MagicMock()
    mock_session.transcript_path = Path("/tmp/e2e_session2.jsonl")
    mock_session_cls.return_value = mock_session

    transcriber = _mock_transcriber()

    async with ScarecrowApp(transcriber=transcriber).run_test() as pilot:
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        app._start_recording()
        await pilot.pause(delay=0.3)
        assert app.state is AppState.RECORDING

        app._append_transcript("verified sentence text")
        await pilot.pause()

        # append_event is called for the divider and for the actual text;
        # just verify the text event made it in.
        calls = [str(c) for c in mock_session.append_event.call_args_list]
        assert any("verified sentence text" in c for c in calls)


# ---------------------------------------------------------------------------
# New tests: batch window timing
# ---------------------------------------------------------------------------


@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_batch_transcription_triggered_at_interval(
    mock_session_cls, mock_recorder_cls
) -> None:
    """_vad_transcribe must call _submit_batch_transcription when RECORDING."""
    mock_recorder = _mock_recorder()
    mock_recorder.drain_to_silence.return_value = np.zeros(16000, dtype=np.float32)
    mock_recorder_cls.return_value = mock_recorder
    mock_session_cls.return_value = MagicMock()

    transcriber = _mock_transcriber()

    async with ScarecrowApp(transcriber=transcriber).run_test() as pilot:
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        app._start_recording()
        await pilot.pause(delay=0.3)
        assert app.state is AppState.RECORDING

        # Pause the VAD poll timer so it doesn't race with our manual call
        if app._batch_timer is not None:
            app._batch_timer.pause()

        submit_calls: list[bool] = []
        original_submit = app._submit_batch_transcription

        def track_submit(audio, batch_elapsed):
            submit_calls.append(True)
            return original_submit(audio, batch_elapsed)

        app._submit_batch_transcription = track_submit  # type: ignore[method-assign]

        # Fire VAD transcribe manually — simulates silence detected
        app._vad_transcribe()
        await pilot.pause()

        assert len(submit_calls) == 1, (
            "_submit_batch_transcription must be called once when _vad_transcribe fires"
        )


@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_batch_transcription_not_triggered_before_interval(
    mock_session_cls, mock_recorder_cls
) -> None:
    """_submit_batch_transcription must NOT be called without _vad_transcribe firing."""
    mock_recorder = _mock_recorder()
    mock_recorder.drain_to_silence.return_value = np.zeros(16000, dtype=np.float32)
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

        # Do NOT call _vad_transcribe — just wait briefly
        await pilot.pause(delay=0.1)

        assert len(submit_calls) == 0, (
            "_submit_batch_transcription must not be called without _vad_transcribe"
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


# ---------------------------------------------------------------------------
# /f flush command tests
# ---------------------------------------------------------------------------


@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_flush_command_drains_buffer(mock_session_cls, mock_recorder_cls) -> None:
    """_handle_flush must drain the audio buffer and submit transcription."""
    mock_rec = _mock_recorder()
    mock_rec.drain_buffer.return_value = np.ones(16000, dtype=np.float32)
    mock_recorder_cls.return_value = mock_rec
    mock_session_cls.return_value = MagicMock()

    transcriber = _mock_transcriber()

    async with _app(with_transcriber=True).run_test() as pilot:
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        app._audio_recorder = mock_rec
        app._transcriber = transcriber
        app.state = AppState.RECORDING

        # Provide a live executor so _submit_batch_transcription can run
        from concurrent.futures import ThreadPoolExecutor

        app._batch_executor = ThreadPoolExecutor(max_workers=1)

        app._handle_flush()
        await pilot.pause(delay=0.3)

        mock_rec.drain_buffer.assert_called_once()
        transcriber.transcribe_batch.assert_called_once()


@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_flush_does_not_lose_audio_when_batch_busy(
    mock_session_cls, mock_recorder_cls
) -> None:
    """_handle_flush must NOT drain the buffer when a batch is in-flight."""
    from concurrent.futures import Future

    mock_rec = _mock_recorder()
    mock_recorder_cls.return_value = mock_rec
    mock_session_cls.return_value = MagicMock()

    transcriber = _mock_transcriber()

    async with _app(with_transcriber=True).run_test() as pilot:
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        app._audio_recorder = mock_rec
        app._transcriber = transcriber
        app.state = AppState.RECORDING

        # Inject an incomplete in-flight future
        inflight: Future[str | None] = Future()
        app._batch_futures = {inflight}

        app._handle_flush()
        await pilot.pause()

        mock_rec.drain_buffer.assert_not_called()


async def test_flush_noop_when_not_recording() -> None:
    """_handle_flush must do nothing when state is IDLE."""
    mock_rec = _mock_recorder()

    async with _app().run_test() as pilot:
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        app._audio_recorder = mock_rec
        app.state = AppState.IDLE

        app._handle_flush()
        await pilot.pause()

        mock_rec.drain_buffer.assert_not_called()


# ---------------------------------------------------------------------------
# Shutdown race: _ignore_batch_results set before _wait_for_batch_workers
# ---------------------------------------------------------------------------


def test_ignore_batch_results_set_before_wait() -> None:
    """_ignore_batch_results must be True by the time _wait_for_batch_workers runs.

    If the flag is set after the wait, a late callback from a worker can still
    call _post_to_ui and duplicate text already captured from the future result.
    """
    flag_at_call_time: list[bool] = []

    def fake_wait():
        flag_at_call_time.append(app._ignore_batch_results)
        return (True, [])

    # Use a ready transcriber so cleanup_after_exit doesn't short-circuit
    app = ScarecrowApp(transcriber=_mock_transcriber())
    app._ignore_batch_results = False
    app._wait_for_batch_workers = fake_wait  # type: ignore[method-assign]

    app.cleanup_after_exit()

    assert flag_at_call_time, "_wait_for_batch_workers was never called"
    assert flag_at_call_time[0] is True, (
        "_ignore_batch_results was False when _wait_for_batch_workers ran; "
        "late callbacks can still duplicate text"
    )


# ---------------------------------------------------------------------------
# Shutdown error path: _flush_final_batch handles transcription errors locally
# ---------------------------------------------------------------------------


def test_flush_final_batch_handles_transcription_error() -> None:
    """_flush_final_batch must catch transcription errors and surface them via
    _show_error rather than letting them propagate through the callback path."""
    import numpy as np

    mock_recorder = _mock_recorder()
    mock_recorder.drain_buffer.return_value = np.zeros(16000, dtype=np.float32)

    transcriber = _mock_transcriber()
    transcriber.transcribe_batch.side_effect = RuntimeError("GPU OOM")

    app = ScarecrowApp(transcriber=transcriber)
    app._audio_recorder = mock_recorder

    errors: list[str] = []
    app._show_error = lambda msg: errors.append(msg)  # type: ignore[method-assign]

    # Must not raise
    app._flush_final_batch(include_ui=True)

    assert len(errors) == 1
    assert "GPU OOM" in errors[0]


# ---------------------------------------------------------------------------
# Session I/O startup failure handling
# ---------------------------------------------------------------------------


@patch("scarecrow.app.Session")
async def test_start_recording_handles_session_creation_failure(
    mock_session_cls,
) -> None:
    """Session creation failure must call _show_error and leave state as IDLE."""
    mock_session_cls.side_effect = OSError("disk full")

    app = ScarecrowApp(transcriber=_mock_transcriber())
    app._preflight_check = lambda: False  # type: ignore[method-assign]
    async with app.run_test() as pilot:
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        app._preflight_check = lambda: True  # type: ignore[method-assign]

        error_messages: list[str] = []
        original_show_error = app._show_error

        def track_error(msg: str, **kwargs) -> None:
            error_messages.append(msg)
            original_show_error(msg, **kwargs)

        app._show_error = track_error  # type: ignore[method-assign]

        app._start_recording()
        await pilot.pause()

        assert app.state is AppState.IDLE, (
            "State must remain IDLE after session failure"
        )
        assert app._session is None, "_session must not be set after failure"
        assert error_messages, (
            "_show_error must be called after session creation failure"
        )


# ---------------------------------------------------------------------------
# Audit gap: _preflight_check failure paths
# ---------------------------------------------------------------------------


@patch("sounddevice.query_devices")
async def test_preflight_check_shows_error_when_no_input_devices(
    mock_qd,
) -> None:
    """_preflight_check must return False and show error when no audio input found."""
    mock_qd.return_value = [{"max_input_channels": 0, "name": "dummy output only"}]

    mock_transcriber = MagicMock()
    mock_transcriber.is_ready = True

    app = ScarecrowApp(transcriber=mock_transcriber)
    async with app.run_test() as pilot:
        await pilot.pause()
        result = app._preflight_check()

    assert result is False
    assert app._status_is_error is True
    assert "No audio input" in app._status_message


@patch("sounddevice.query_devices")
async def test_preflight_check_shows_error_when_device_query_fails(
    mock_qd,
) -> None:
    """_preflight_check must return False and show error when query_devices raises."""
    mock_qd.side_effect = OSError("no audio subsystem")

    mock_transcriber = MagicMock()
    mock_transcriber.is_ready = True

    app = ScarecrowApp(transcriber=mock_transcriber)
    async with app.run_test() as pilot:
        await pilot.pause()
        result = app._preflight_check()

    assert result is False
    assert app._status_is_error is True
    assert "Could not query" in app._status_message


# ---------------------------------------------------------------------------
# Audit gap: _check_recorder_warnings surfaces recorder warnings
# ---------------------------------------------------------------------------


async def test_check_recorder_warnings_surfaces_warning() -> None:
    """_check_recorder_warnings must write recorder warnings to the transcript pane."""
    app = ScarecrowApp()
    async with app.run_test() as pilot:
        await pilot.pause()

        mock_rec = MagicMock()
        mock_rec._last_warning = "Audio input overflow"
        mock_rec._disk_write_failed = False
        app._audio_recorder = mock_rec
        app._session = None

        captions = app.query_one("#captions", RichLog)
        initial_lines = len(captions.lines)

        app._check_recorder_warnings()
        await pilot.pause()

        assert len(captions.lines) > initial_lines
        text = " ".join(str(line) for line in captions.lines[initial_lines:])
        assert "Audio input overflow" in text
        assert mock_rec._last_warning is None  # consumed


# ---------------------------------------------------------------------------
# Audit gap: action_pause handles device-loss exceptions from stream.pause/resume
# ---------------------------------------------------------------------------


@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_pause_handles_stream_stop_failure(
    mock_session_cls, mock_recorder_cls
) -> None:
    """action_pause must not crash if recorder.pause() raises (e.g. device lost)."""
    mock_rec = _mock_recorder()
    mock_rec.pause.side_effect = OSError("device disconnected")
    mock_recorder_cls.return_value = mock_rec
    mock_session_cls.return_value = MagicMock()

    app = ScarecrowApp(transcriber=_mock_transcriber())
    app._preflight_check = lambda: True  # type: ignore[method-assign]
    async with app.run_test() as pilot:
        app._start_recording()
        await pilot.pause(delay=0.3)
        assert app.state is AppState.RECORDING

        # Must not raise
        app.action_pause()
        await pilot.pause()

        # State transitions to PAUSED regardless
        assert app.state is AppState.PAUSED


@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_resume_handles_stream_start_failure(
    mock_session_cls, mock_recorder_cls
) -> None:
    """action_pause (resume branch) must not crash if recorder.resume() raises."""
    mock_rec = _mock_recorder()
    mock_rec.resume.side_effect = OSError("device gone")
    mock_recorder_cls.return_value = mock_rec
    mock_session_cls.return_value = MagicMock()

    app = ScarecrowApp(transcriber=_mock_transcriber())
    app._preflight_check = lambda: True  # type: ignore[method-assign]
    async with app.run_test() as pilot:
        app._start_recording()
        await pilot.pause(delay=0.3)

        # Pause first (recorder.pause works fine here)
        mock_rec.pause.side_effect = None
        app.action_pause()
        await pilot.pause()
        assert app.state is AppState.PAUSED

        # Now resume — recorder.resume raises
        app.action_pause()
        await pilot.pause()

        # State transitions to RECORDING regardless
        assert app.state is AppState.RECORDING


# ---------------------------------------------------------------------------
# M1: All-silent buffer must not accumulate — drain_to_silence discards it
# ---------------------------------------------------------------------------


def test_all_silent_buffer_does_not_accumulate(tmp_path: Path) -> None:
    """drain_to_silence must return None and clear buffer when all chunks are silent.

    Regression: silence_end == 0 (buffer starts with silence) previously
    skipped the drain because the guard was `silence_end > 0`, causing the
    buffer to accumulate for up to 30 seconds before a hard drain.
    """
    from unittest.mock import MagicMock, patch

    from scarecrow.recorder import AudioRecorder

    with patch.dict(
        "sys.modules", {"sounddevice": MagicMock(), "soundfile": MagicMock()}
    ):
        recorder = AudioRecorder(tmp_path / "audio.wav")

    # Inject all-silent chunks (> 0.75s so the VAD_MIN_SILENCE_MS guard passes)
    # At 16kHz with 1600-sample chunks, 8 chunks = 0.8s
    silent_chunk = np.zeros((1600, 1), dtype="int16")
    with recorder._buffer_lock:
        for _ in range(8):
            recorder._audio_chunks.append(silent_chunk.copy())
            recorder._chunk_energies.append(0.0)

    result = recorder.drain_to_silence()

    # All-silent buffer must be discarded (not sent to transcriber)
    assert result is None, "All-silent buffer must return None"
    with recorder._buffer_lock:
        assert len(recorder._audio_chunks) == 0, "Buffer must be cleared"
        assert len(recorder._chunk_energies) == 0


# ---------------------------------------------------------------------------
# M2: action_pause must flush remaining pre-pause audio buffer
# ---------------------------------------------------------------------------


@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_pause_flushes_remaining_audio_buffer(
    mock_session_cls, mock_recorder_cls
) -> None:
    """action_pause must call drain_buffer() to flush any audio VAD didn't drain.

    Regression: pre-pause audio without a silence boundary was left in the
    buffer and would bleed into the post-resume transcription window.
    """
    mock_rec = _mock_recorder()
    # drain_to_silence returns None (no silence boundary found)
    mock_rec.drain_to_silence.return_value = None
    # drain_buffer returns leftover speech audio
    leftover = np.zeros(8000, dtype=np.float32)
    mock_rec.drain_buffer.return_value = leftover
    mock_recorder_cls.return_value = mock_rec
    mock_session_cls.return_value = MagicMock()

    app = ScarecrowApp(transcriber=_mock_transcriber())
    app._preflight_check = lambda: True  # type: ignore[method-assign]
    async with app.run_test() as pilot:
        app._start_recording()
        await pilot.pause(delay=0.3)
        assert app.state is AppState.RECORDING

        app.action_pause()
        await pilot.pause()

        assert app.state is AppState.PAUSED
        # drain_buffer must have been called to flush pre-pause audio
        mock_rec.drain_buffer.assert_called()


# ---------------------------------------------------------------------------
# JSONL timestamp standardization: all events must have timestamp + elapsed
# ---------------------------------------------------------------------------


async def test_transcript_event_has_timestamp_and_elapsed(tmp_path: Path) -> None:
    """transcript events written by _record_transcript must include both
    an ISO 8601 timestamp field and an elapsed field."""
    import json
    import re

    from scarecrow.session import Session

    real_session = Session(base_dir=tmp_path)

    async with _app().run_test() as pilot:
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        await pilot.pause()

        app._session = real_session
        app._elapsed = 42
        app._last_divider_elapsed = -999  # force a divider first time
        app._append_transcript("test transcript text")
        await pilot.pause()

    events = [
        json.loads(line)
        for line in real_session.transcript_path.read_text().splitlines()
    ]
    transcript_events = [e for e in events if e.get("type") == "transcript"]
    assert transcript_events, "Expected at least one transcript event"
    ev = transcript_events[0]
    assert "elapsed" in ev, "transcript event must have 'elapsed' field"
    assert "timestamp" in ev, "transcript event must have 'timestamp' field"
    pattern = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$"
    assert re.match(pattern, ev["timestamp"]), (
        f"transcript timestamp {ev['timestamp']!r} does not match ISO 8601"
    )


async def test_divider_event_has_timestamp_and_elapsed(tmp_path: Path) -> None:
    """divider events must include both an ISO 8601 timestamp field and elapsed."""
    import json
    import re

    from scarecrow.session import Session

    real_session = Session(base_dir=tmp_path)

    async with _app().run_test() as pilot:
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        await pilot.pause()

        app._session = real_session
        app._elapsed = 10
        app._last_divider_elapsed = -999  # force a divider
        app._append_transcript("divider test")
        await pilot.pause()

    events = [
        json.loads(line)
        for line in real_session.transcript_path.read_text().splitlines()
    ]
    divider_events = [e for e in events if e.get("type") == "divider"]
    assert divider_events, "Expected at least one divider event"
    ev = divider_events[0]
    assert "elapsed" in ev, "divider event must have 'elapsed' field"
    assert "timestamp" in ev, "divider event must have 'timestamp' field"
    pattern = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$"
    assert re.match(pattern, ev["timestamp"]), (
        f"divider timestamp {ev['timestamp']!r} does not match ISO 8601"
    )


async def test_pause_event_has_timestamp_and_elapsed(tmp_path: Path) -> None:
    """pause events must include both an ISO 8601 timestamp field and elapsed."""
    import json
    import re

    from scarecrow.session import Session

    real_session = Session(base_dir=tmp_path)

    async with _app().run_test() as pilot:
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        await pilot.pause()

        app._session = real_session
        app._elapsed = 77
        app.state = AppState.RECORDING
        app._write_pause_marker()
        await pilot.pause()

    events = [
        json.loads(line)
        for line in real_session.transcript_path.read_text().splitlines()
    ]
    pause_events = [e for e in events if e.get("type") == "pause"]
    assert pause_events, "Expected at least one pause event"
    ev = pause_events[0]
    assert "elapsed" in ev, "pause event must have 'elapsed' field"
    assert "timestamp" in ev, "pause event must have 'timestamp' field"
    pattern = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$"
    assert re.match(pattern, ev["timestamp"]), (
        f"pause timestamp {ev['timestamp']!r} does not match ISO 8601"
    )


async def test_note_event_has_timestamp_and_elapsed(tmp_path: Path) -> None:
    """note events must include both an ISO 8601 timestamp field and elapsed."""
    import json
    import re

    from scarecrow.session import Session

    real_session = Session(base_dir=tmp_path)

    async with _app().run_test() as pilot:
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        await pilot.pause()

        app._session = real_session
        app._elapsed = 55
        input_widget = app.query_one("#note-input", Input)
        input_widget.value = "note with timestamp"

        app._submit_note()
        await pilot.pause()

    events = [
        json.loads(line)
        for line in real_session.transcript_path.read_text().splitlines()
    ]
    note_events = [e for e in events if e.get("type") == "note"]
    assert note_events, "Expected at least one note event"
    ev = note_events[0]
    assert "elapsed" in ev, "note event must have 'elapsed' field"
    assert "timestamp" in ev, "note event must have 'timestamp' field"
    pattern = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$"
    assert re.match(pattern, ev["timestamp"]), (
        f"note timestamp {ev['timestamp']!r} does not match ISO 8601"
    )


async def test_warning_event_has_timestamp_and_elapsed(tmp_path: Path) -> None:
    """warning events must include both an ISO 8601 timestamp field and elapsed."""
    import json
    import re

    from scarecrow.session import Session

    real_session = Session(base_dir=tmp_path)

    async with _app().run_test() as pilot:
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        await pilot.pause()

        app._session = real_session
        app._elapsed = 30
        app._warn_transcript("test warning message")
        await pilot.pause()

    events = [
        json.loads(line)
        for line in real_session.transcript_path.read_text().splitlines()
    ]
    warning_events = [e for e in events if e.get("type") == "warning"]
    assert warning_events, "Expected at least one warning event"
    ev = warning_events[0]
    assert "elapsed" in ev, "warning event must have 'elapsed' field"
    assert "timestamp" in ev, "warning event must have 'timestamp' field"
    pattern = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$"
    assert re.match(pattern, ev["timestamp"]), (
        f"warning timestamp {ev['timestamp']!r} does not match ISO 8601"
    )


async def test_resume_event_written_on_unpause(tmp_path: Path) -> None:
    """Pressing Ctrl+P to resume from PAUSED must write a resume event to the
    transcript with both an ISO 8601 timestamp and an elapsed field."""
    import json
    import re

    from scarecrow.session import Session

    real_session = Session(base_dir=tmp_path)

    async with _app().run_test() as pilot:
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        await pilot.pause()

        app._session = real_session
        app._elapsed = 120
        # Manually set to PAUSED so action_pause takes the resume branch
        app.state = AppState.PAUSED

        app.action_pause()
        await pilot.pause()

        assert app.state is AppState.RECORDING

    events = [
        json.loads(line)
        for line in real_session.transcript_path.read_text().splitlines()
    ]
    resume_events = [e for e in events if e.get("type") == "resume"]
    assert resume_events, "Expected a resume event after unpausing"
    ev = resume_events[0]
    assert "elapsed" in ev, "resume event must have 'elapsed' field"
    assert ev["elapsed"] == 120
    assert "timestamp" in ev, "resume event must have 'timestamp' field"
    pattern = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$"
    assert re.match(pattern, ev["timestamp"]), (
        f"resume timestamp {ev['timestamp']!r} does not match ISO 8601"
    )


@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_mn_command_renames_session(mock_session_cls, mock_recorder_cls) -> None:
    """The /mn command must call session.rename() with the provided name."""
    mock_rec = _mock_recorder()
    mock_recorder_cls.return_value = mock_rec
    mock_session = MagicMock()
    mock_session_cls.return_value = mock_session

    app = ScarecrowApp(transcriber=_mock_transcriber())
    app._preflight_check = lambda: True  # type: ignore[method-assign]
    async with app.run_test() as pilot:
        app._start_recording()
        await pilot.pause(delay=0.3)

        input_widget = app.query_one("#note-input", Input)
        input_widget.value = "/mn Standup with Team"
        await pilot.press("enter")
        await pilot.pause()

        mock_session.rename.assert_called_once_with("Standup with Team")
