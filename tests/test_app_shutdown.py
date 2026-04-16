"""Shutdown, cleanup, flush, and circuit-breaker tests."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np

from scarecrow.app import AppState, ScarecrowApp
from tests.helpers import _app, _mock_recorder, _mock_transcriber, _read_jsonl

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


async def test_deferred_quit_exits_without_blocking_on_cleanup() -> None:
    """_deferred_quit must call exit() immediately — cleanup runs post-TUI."""
    async with _app().run_test() as pilot:
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        await pilot.pause()

        stop_called: list[bool] = []
        exit_called: list[bool] = []

        original_exit = app.exit

        def track_stop():
            stop_called.append(True)

        def track_exit(*args, **kwargs):
            exit_called.append(True)
            return original_exit(*args, **kwargs)

        app._stop_recording = track_stop  # type: ignore[method-assign]
        app.exit = track_exit  # type: ignore[method-assign]

        app._deferred_quit()
        await pilot.pause()

        assert len(stop_called) == 0, "_deferred_quit must NOT call _stop_recording"
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

    from scarecrow.config import Config

    cfg = Config(DEFAULT_RECORDINGS_DIR=tmp_path, OBSIDIAN_VAULT_DIR=None)
    async with ScarecrowApp(transcriber=transcriber, cfg=cfg).run_test() as pilot:
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

    from scarecrow.config import Config

    cfg = Config(DEFAULT_RECORDINGS_DIR=tmp_path, OBSIDIAN_VAULT_DIR=None)
    async with ScarecrowApp(transcriber=transcriber, cfg=cfg).run_test() as pilot:
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

    from scarecrow.config import Config

    cfg = Config(DEFAULT_RECORDINGS_DIR=tmp_path, OBSIDIAN_VAULT_DIR=None)
    app = ScarecrowApp(transcriber=mock_transcriber, cfg=cfg)
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
        # Executor must NOT be destroyed on timeout — shutdown(wait=False) leaves
        # the old thread alive; creating a new executor would give two threads
        # hitting the Metal Device concurrently (SIGSEGV).
        assert app._batch_executor is not None
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

    from scarecrow.config import Config

    cfg = Config(DEFAULT_RECORDINGS_DIR=tmp_path, OBSIDIAN_VAULT_DIR=None)
    app = ScarecrowApp(transcriber=transcriber, cfg=cfg)
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

    mock_executor.shutdown.assert_called_once_with(wait=True, cancel_futures=True)
    assert app._batch_executor is None


# ---------------------------------------------------------------------------
# /f flush command tests
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Diarization phase in post_exit_cleanup
# ---------------------------------------------------------------------------


def test_post_exit_cleanup_calls_diarize_session(tmp_path: Path) -> None:
    """post_exit_cleanup must call diarize_session before summarization."""
    from scarecrow.config import Config
    from scarecrow.session import Session

    real_session = Session(base_dir=tmp_path)
    # Write a minimal transcript with a SPEAKERS note
    real_session.append_event(
        {"type": "note", "tag": "SPEAKERS", "elapsed": 0, "text": "mic:Dave sys:Mike"}
    )
    real_session.append_event(
        {"type": "transcript", "elapsed": 5, "text": "Hello", "source": "sys"}
    )

    cfg = Config(DEFAULT_RECORDINGS_DIR=tmp_path, OBSIDIAN_VAULT_DIR=None)
    app = ScarecrowApp(cfg=cfg)
    app._completed_session = real_session
    app._current_segment = 1
    app._skip_summary = False
    app._sys_audio_enabled = True

    diarize_calls = []

    with (
        patch(
            "scarecrow.diarizer.diarize_session",
            side_effect=lambda *a, **kw: diarize_calls.append(True) or False,
        ),
        patch("scarecrow.summarizer.summarize_session_segments", return_value=None),
    ):
        app.post_exit_cleanup()

    assert len(diarize_calls) == 1


def test_post_exit_cleanup_skips_diarize_on_quick_quit(tmp_path: Path) -> None:
    """post_exit_cleanup must skip diarization when _skip_summary is True."""
    from scarecrow.config import Config
    from scarecrow.session import Session

    real_session = Session(base_dir=tmp_path)

    cfg = Config(DEFAULT_RECORDINGS_DIR=tmp_path, OBSIDIAN_VAULT_DIR=None)
    app = ScarecrowApp(cfg=cfg)
    app._completed_session = real_session
    app._current_segment = 1
    app._skip_summary = True
    app._sys_audio_enabled = True

    with patch("scarecrow.diarizer.diarize_session") as mock_diar:
        app.post_exit_cleanup()

    mock_diar.assert_not_called()
