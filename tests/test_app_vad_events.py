"""VAD gating, transcript event writing, and JSONL timestamp tests."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
from textual.widgets import RichLog

from scarecrow.app import AppState, InfoBar, ScarecrowApp
from tests.helpers import _app, _mock_recorder, _mock_transcriber

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
# New tests: batch window timing
# ---------------------------------------------------------------------------


@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_batch_transcription_triggered_at_interval(
    mock_session_cls, mock_recorder_cls
) -> None:
    """_vad_transcribe must call _submit_batch_transcription when RECORDING."""
    mock_recorder = _mock_recorder()
    mock_recorder.drain_to_silence.return_value = (
        np.zeros(16000, dtype=np.float32),
        [0.05] * 10,
    )
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

        # Clear any mic future set by a timer-triggered call during the
        # pause above — prevents _reap_source_future from returning False.
        app._mic_future = None

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
    mock_recorder.drain_to_silence.return_value = (
        np.zeros(16000, dtype=np.float32),
        [0.05] * 10,
    )
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
# Speech-ratio gate: low-floor path filtering
# ---------------------------------------------------------------------------


@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_vad_skips_near_silent_mic_audio(
    mock_session_cls, mock_recorder_cls
) -> None:
    """_vad_transcribe must skip buffers where all chunks are near-silent.

    Regression: low_floor (0.05x threshold) was so permissive that noise
    passed the gate, causing Parakeet to hallucinate on silence.
    """
    mock_recorder = _mock_recorder()
    # All chunk energies well below the low floor (0.15 * 0.01 = 0.0015)
    mock_recorder.drain_to_silence.return_value = (
        np.zeros(16000, dtype=np.float32),
        [0.0005] * 10,  # noise — below low_floor
    )
    mock_recorder_cls.return_value = mock_recorder
    mock_session_cls.return_value = MagicMock()

    transcriber = _mock_transcriber()

    async with ScarecrowApp(transcriber=transcriber).run_test() as pilot:
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        app._start_recording()
        await pilot.pause(delay=0.3)
        assert app.state is AppState.RECORDING

        if app._batch_timer is not None:
            app._batch_timer.pause()

        # Clear any mic future set by a timer-triggered call during the
        # pause above — ensures the skip is from the speech gate, not a busy future.
        app._mic_future = None

        submit_calls: list[bool] = []
        original_submit = app._submit_batch_transcription

        def track_submit(audio, batch_elapsed):
            submit_calls.append(True)
            return original_submit(audio, batch_elapsed)

        app._submit_batch_transcription = track_submit  # type: ignore[method-assign]

        app._vad_transcribe()
        await pilot.pause()

        assert len(submit_calls) == 0, (
            "Near-silent audio must be skipped by the speech-ratio gate"
        )


@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_vad_low_floor_requires_higher_speech_ratio(
    mock_session_cls, mock_recorder_cls
) -> None:
    """Low-floor path requires 2x the normal speech ratio (30% not 15%).

    Simulates a phone call: most chunks are below the primary floor (0.005)
    but above the low floor (0.0015). With only 20% of chunks having speech
    at the low-floor level, the buffer must be skipped.
    """
    mock_recorder = _mock_recorder()
    # 2 out of 10 chunks above low_floor (20%), 8 below
    # 20% is above the normal ratio (15%) but below the 2x ratio (30%)
    energies = [0.002] * 2 + [0.0005] * 8
    mock_recorder.drain_to_silence.return_value = (
        np.zeros(16000, dtype=np.float32),
        energies,
    )
    mock_recorder_cls.return_value = mock_recorder
    mock_session_cls.return_value = MagicMock()

    transcriber = _mock_transcriber()

    async with ScarecrowApp(transcriber=transcriber).run_test() as pilot:
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        app._start_recording()
        await pilot.pause(delay=0.3)
        assert app.state is AppState.RECORDING

        if app._batch_timer is not None:
            app._batch_timer.pause()

        # Clear any mic future set by a timer-triggered call during the
        # pause above — ensures the skip is from the speech gate, not a busy future.
        app._mic_future = None

        submit_calls: list[bool] = []
        original_submit = app._submit_batch_transcription

        def track_submit(audio, batch_elapsed):
            submit_calls.append(True)
            return original_submit(audio, batch_elapsed)

        app._submit_batch_transcription = track_submit  # type: ignore[method-assign]

        app._vad_transcribe()
        await pilot.pause()

        assert len(submit_calls) == 0, (
            "Low-floor path must require 2x speech ratio (30%), 20% should not pass"
        )


@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_vad_low_floor_passes_with_sufficient_speech(
    mock_session_cls, mock_recorder_cls
) -> None:
    """Low-floor path proceeds when enough chunks have detectable speech.

    Simulates a phone call with genuine speech: 40% of chunks above the
    low floor, which exceeds the 2x ratio (30%).
    """
    mock_recorder = _mock_recorder()
    # 4 out of 10 chunks above low_floor (40%), 6 below
    energies = [0.002] * 4 + [0.0005] * 6
    mock_recorder.drain_to_silence.return_value = (
        np.zeros(16000, dtype=np.float32),
        energies,
    )
    mock_recorder_cls.return_value = mock_recorder
    mock_session_cls.return_value = MagicMock()

    transcriber = _mock_transcriber()

    async with ScarecrowApp(transcriber=transcriber).run_test() as pilot:
        app: ScarecrowApp = pilot.app  # type: ignore[assignment]
        app._start_recording()
        await pilot.pause(delay=0.3)
        assert app.state is AppState.RECORDING

        if app._batch_timer is not None:
            app._batch_timer.pause()

        # Clear any mic future set by a timer-triggered call during the
        # pause above — prevents _reap_source_future from returning False.
        app._mic_future = None

        submit_calls: list[bool] = []
        original_submit = app._submit_batch_transcription

        def track_submit(audio, batch_elapsed):
            submit_calls.append(True)
            return original_submit(audio, batch_elapsed)

        app._submit_batch_transcription = track_submit  # type: ignore[method-assign]

        app._vad_transcribe()
        await pilot.pause()

        assert len(submit_calls) == 1, (
            "Low-floor path must proceed when speech ratio exceeds 2x threshold"
        )


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

    from scarecrow.config import Config
    from scarecrow.recorder import AudioRecorder

    cfg = Config(SAMPLE_RATE=16000, RECORDING_SAMPLE_RATE=16000)
    with patch.dict(
        "sys.modules", {"sounddevice": MagicMock(), "soundfile": MagicMock()}
    ):
        recorder = AudioRecorder(tmp_path / "audio.wav", sample_rate=16000, cfg=cfg)

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
