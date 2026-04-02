"""Recording state transitions, preflight, and playback tests."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
from textual.widgets import RichLog

from scarecrow.app import AppState, ScarecrowApp
from scarecrow.session import Session  # noqa: F401 — used in divider tests via @patch
from tests.helpers import _app, _mock_recorder, _mock_transcriber

# ---------------------------------------------------------------------------
# Stop recording transitions
# ---------------------------------------------------------------------------


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
# Stop recording flush / finalize ordering
# ---------------------------------------------------------------------------


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
        app._batch_executor = ThreadPoolExecutor(max_workers=1)

        app._handle_flush()
        await pilot.pause(delay=0.3)

        mock_rec.drain_buffer.assert_called_once()
        transcriber.transcribe_batch.assert_called_once()


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
# Divider throttle tests
# ---------------------------------------------------------------------------


@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_divider_throttle_skips_intermediate_batches(
    mock_session_cls, mock_recorder_cls
) -> None:
    """Only batches >= DIVIDER_INTERVAL apart should emit a divider line."""
    mock_recorder_cls.return_value = _mock_recorder()
    mock_session = MagicMock()
    mock_session.transcript_path = Path("/tmp/test_throttle.txt")
    mock_session_cls.return_value = mock_session

    app = ScarecrowApp(transcriber=_mock_transcriber())
    app._preflight_check = lambda: True  # type: ignore[method-assign]

    async with app.run_test() as pilot:
        await pilot.press("enter")
        await pilot.pause(delay=0.3)

        captions = app.query_one("#captions", RichLog)
        initial_lines = len(captions.lines)

        # Call _record_transcript at elapsed values 0..60 in steps of 10
        # DIVIDER_INTERVAL=60: dividers expected at elapsed=0 and elapsed=60
        for elapsed in [0, 10, 20, 30, 40, 50, 60]:
            app._record_transcript("word", batch_elapsed=elapsed)
        await pilot.pause()

        new_lines = [str(line) for line in captions.lines[initial_lines:]]
        divider_lines = [ln for ln in new_lines if "test_throttle.txt" in ln]
        assert len(divider_lines) == 2, (
            f"Expected 2 dividers (at elapsed=0 and elapsed=60), "
            f"got {len(divider_lines)}: {divider_lines}"
        )


@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_divider_appears_after_pause_resume(
    mock_session_cls, mock_recorder_cls
) -> None:
    """After resume, _last_divider_elapsed is reset so the next batch gets a divider."""
    from scarecrow import config

    mock_recorder_cls.return_value = _mock_recorder()
    mock_session = MagicMock()
    mock_session.transcript_path = Path("/tmp/test_resume.txt")
    mock_session_cls.return_value = mock_session

    app = ScarecrowApp(transcriber=_mock_transcriber())
    app._preflight_check = lambda: True  # type: ignore[method-assign]

    async with app.run_test() as pilot:
        await pilot.press("enter")
        await pilot.pause(delay=0.3)

        captions = app.query_one("#captions", RichLog)
        initial_lines = len(captions.lines)

        # First transcript at elapsed=0 — should get a divider
        app._record_transcript("first", batch_elapsed=0)
        await pilot.pause()

        lines_after_first = len(captions.lines)
        first_new = [str(ln) for ln in captions.lines[initial_lines:]]
        assert any("test_resume.txt" in ln for ln in first_new), (
            "Expected a divider after first transcript"
        )

        # Simulate pause then resume (resets _last_divider_elapsed)
        app.state = AppState.PAUSED
        app._last_divider_elapsed = (
            -config.DIVIDER_INTERVAL
        )  # mirrors pause/resume reset

        # Now at elapsed=5 — should get a divider because timer was reset
        app._record_transcript("second", batch_elapsed=5)
        await pilot.pause()

        new_after_resume = [str(ln) for ln in captions.lines[lines_after_first:]]
        assert any("test_resume.txt" in ln for ln in new_after_resume), (
            "Expected a divider after resume at elapsed=5"
        )
