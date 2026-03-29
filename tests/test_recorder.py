"""Unit tests for AudioRecorder using mocked sounddevice."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from scarecrow.recorder import AudioRecorder


@pytest.fixture()
def output_path(tmp_path: Path) -> Path:
    return tmp_path / "audio.wav"


@pytest.fixture()
def mock_sd():
    """Patch sounddevice so no real audio hardware is touched."""
    mock = MagicMock()
    mock_stream = MagicMock()
    mock.InputStream.return_value = mock_stream
    with patch.dict("sys.modules", {"sounddevice": mock}):
        yield mock, mock_stream


@pytest.fixture()
def mock_sf():
    """Patch soundfile so no real files are written."""
    mock = MagicMock()
    mock_file = MagicMock()
    mock.SoundFile.return_value = mock_file
    with patch.dict("sys.modules", {"soundfile": mock}):
        yield mock, mock_file


def test_initial_state_not_recording(output_path: Path, mock_sd, mock_sf) -> None:
    """Recorder starts in a non-recording, non-paused state."""
    recorder = AudioRecorder(output_path)
    assert not recorder.is_recording
    assert not recorder.is_paused


def test_start_transitions_to_recording(output_path: Path, mock_sd, mock_sf) -> None:
    """start() transitions is_recording to True."""
    recorder = AudioRecorder(output_path)
    recorder.start()
    assert recorder.is_recording
    assert not recorder.is_paused


def test_pause_transitions_to_paused(output_path: Path, mock_sd, mock_sf) -> None:
    """pause() sets is_paused to True while still recording."""
    recorder = AudioRecorder(output_path)
    recorder.start()
    recorder.pause()
    assert recorder.is_recording
    assert recorder.is_paused


def test_resume_clears_paused(output_path: Path, mock_sd, mock_sf) -> None:
    """resume() clears is_paused."""
    recorder = AudioRecorder(output_path)
    recorder.start()
    recorder.pause()
    recorder.resume()
    assert recorder.is_recording
    assert not recorder.is_paused


def test_stop_transitions_to_stopped(output_path: Path, mock_sd, mock_sf) -> None:
    """stop() sets is_recording to False."""
    recorder = AudioRecorder(output_path)
    recorder.start()
    recorder.stop()
    assert not recorder.is_recording


def test_stop_returns_output_path(output_path: Path, mock_sd, mock_sf) -> None:
    """stop() returns the WAV file path."""
    recorder = AudioRecorder(output_path)
    recorder.start()
    result = recorder.stop()
    assert result == output_path


def test_start_opens_soundfile_with_correct_params(
    output_path: Path, mock_sd, mock_sf
) -> None:
    """start() opens SoundFile with PCM_16 subtype, correct sample rate and channels."""
    mock_sf_module, _ = mock_sf
    recorder = AudioRecorder(output_path, sample_rate=44100, channels=1)
    recorder.start()
    mock_sf_module.SoundFile.assert_called_once_with(
        output_path,
        mode="w",
        samplerate=44100,
        channels=1,
        subtype="PCM_16",
    )
    recorder.stop()


def test_start_opens_input_stream(output_path: Path, mock_sd, mock_sf) -> None:
    """start() opens a sounddevice InputStream."""
    mock_sd_module, mock_stream = mock_sd
    recorder = AudioRecorder(output_path, sample_rate=44100, channels=1)
    recorder.start()
    mock_sd_module.InputStream.assert_called_once()
    mock_stream.start.assert_called_once()
    recorder.stop()


def test_stop_closes_stream_and_file(output_path: Path, mock_sd, mock_sf) -> None:
    """stop() closes both the stream and the sound file."""
    _, mock_stream = mock_sd
    _, mock_file = mock_sf
    recorder = AudioRecorder(output_path)
    recorder.start()
    recorder.stop()
    mock_stream.stop.assert_called_once()
    mock_stream.close.assert_called_once()
    mock_file.close.assert_called_once()


def test_callback_writes_audio_when_recording(
    output_path: Path, mock_sd, mock_sf
) -> None:
    """Callback writes mic data to SoundFile when not paused."""
    _, mock_file = mock_sf
    recorder = AudioRecorder(output_path)
    recorder.start()

    indata = np.zeros((1024, 1), dtype="int16")
    recorder._callback(indata, 1024, None, None)

    mock_file.write.assert_called_once_with(indata)
    recorder.stop()


def test_callback_writes_silence_when_paused(
    output_path: Path, mock_sd, mock_sf
) -> None:
    """Callback writes zeros to SoundFile when paused."""
    _, mock_file = mock_sf
    recorder = AudioRecorder(output_path)
    recorder.start()
    recorder.pause()

    indata = np.ones((1024, 1), dtype="int16") * 1000
    recorder._callback(indata, 1024, None, None)

    written = mock_file.write.call_args[0][0]
    assert np.all(written == 0)
    recorder.stop()


def test_callback_does_nothing_when_stopped(
    output_path: Path, mock_sd, mock_sf
) -> None:
    """Callback does not write after stop() is called."""
    _, mock_file = mock_sf
    recorder = AudioRecorder(output_path)
    recorder.start()
    recorder.stop()

    indata = np.zeros((1024, 1), dtype="int16")
    recorder._callback(indata, 1024, None, None)

    mock_file.write.assert_not_called()


def test_start_is_idempotent(output_path: Path, mock_sd, mock_sf) -> None:
    """Calling start() twice does not open a second stream."""
    mock_sd_module, _ = mock_sd
    recorder = AudioRecorder(output_path)
    recorder.start()
    recorder.start()
    assert mock_sd_module.InputStream.call_count == 1
    recorder.stop()


def test_peak_level_returns_correct_value(output_path: Path, mock_sd, mock_sf) -> None:
    """peak_level property returns the value set by the callback, under lock."""
    recorder = AudioRecorder(output_path)
    recorder.start()

    indata = np.full((1024, 1), 32767, dtype="int16")
    recorder._callback(indata, 1024, None, None)

    assert recorder.peak_level == pytest.approx(32767 / 32768.0)
    recorder.stop()


def test_drain_to_silence_returns_none_when_empty(
    output_path: Path, mock_sd, mock_sf
) -> None:
    """drain_to_silence must return None when the audio buffer is empty."""
    recorder = AudioRecorder(output_path)
    recorder.start()

    result = recorder.drain_to_silence()

    assert result is None
    recorder.stop()


def test_drain_to_silence_drains_at_silence_boundary(
    output_path: Path, mock_sd, mock_sf
) -> None:
    """drain_to_silence must return audio up to the silence boundary."""
    recorder = AudioRecorder(output_path)
    recorder.start()

    # Inject loud chunks followed by silent ones.
    # VAD_MIN_SILENCE_MS=600ms; at 16000 Hz with 1600-sample chunks (0.1s each),
    # min_silent_chunks = ceil(600ms / 100ms) = 6.  We need 7 loud + 6 silent
    # so the silence boundary is found mid-buffer (silence_end > 0).
    loud_chunk = np.full((1600, 1), 16000, dtype="int16")
    silent_chunk = np.zeros((1600, 1), dtype="int16")

    with recorder._buffer_lock:
        for _ in range(7):
            recorder._audio_chunks.append(loud_chunk.copy())
            recorder._chunk_energies.append(0.5)  # loud — well above threshold
        for _ in range(6):
            recorder._audio_chunks.append(silent_chunk.copy())
            recorder._chunk_energies.append(0.0)  # silent — below threshold

    result = recorder.drain_to_silence()

    # Should have drained something (loud portion up to or through silence)
    assert result is not None
    assert len(result) > 0
    recorder.stop()


def test_buffer_seconds_reflects_buffered_audio(
    output_path: Path, mock_sd, mock_sf
) -> None:
    """buffer_seconds must return the correct duration based on buffered audio."""
    recorder = AudioRecorder(output_path)
    recorder.start()

    # Inject 1600 samples at 16000 Hz = 0.1 seconds
    chunk = np.zeros((1600, 1), dtype="int16")
    with recorder._buffer_lock:
        recorder._audio_chunks.append(chunk)
        recorder._chunk_energies.append(0.0)

    assert recorder.buffer_seconds == pytest.approx(0.1)
    recorder.stop()


def test_drain_to_silence_uses_ceil_for_silence_chunks(
    output_path: Path, mock_sd, mock_sf
) -> None:
    """drain_to_silence must use ceiling division for min_silent_chunks.

    Regression: BUG floor division underestimates required silence with
    non-even chunk sizes. With 1700-sample chunks at 16kHz, 600ms silence
    requires ceil(9600/1700)=6 chunks, not floor(9600/1700)=5.
    """
    recorder = AudioRecorder(output_path)
    recorder.start()

    # Use 1700-sample chunks (non-even division with 600ms silence at 16kHz)
    # min_silence_samples = 16000 * 600 / 1000 = 9600
    # floor(9600 / 1700) = 5 chunks = 531ms (WRONG - less than 600ms)
    # ceil(9600 / 1700) = 6 chunks = 637ms (CORRECT - at least 600ms)
    loud_chunk = np.full((1700, 1), 16000, dtype="int16")
    silent_chunk = np.zeros((1700, 1), dtype="int16")

    # 7 loud chunks + exactly 5 silent chunks
    # With floor division (bug): 5 >= 5, would drain
    # With ceil division (fix): 5 < 6, should NOT drain (not enough silence)
    with recorder._buffer_lock:
        for _ in range(7):
            recorder._audio_chunks.append(loud_chunk.copy())
            recorder._chunk_energies.append(0.5)
        for _ in range(5):
            recorder._audio_chunks.append(silent_chunk.copy())
            recorder._chunk_energies.append(0.0)

    # Total audio: 12 * 1700 / 16000 = 1.275s (above 0.5s minimum)
    # But only 5 silent chunks = 531ms < 600ms required
    # With the ceil fix, this should NOT drain (returns None or hard-drain if > max)
    # Since 1.275s < 30s max, it should return None
    result = recorder.drain_to_silence(min_silence_ms=600)
    assert result is None, (
        "drain_to_silence with 5 silent chunks at 1700 samples should NOT drain "
        "(ceil(9600/1700)=6 chunks required, only 5 provided)"
    )

    # Now add the 6th silent chunk — should drain
    with recorder._buffer_lock:
        recorder._audio_chunks.append(silent_chunk.copy())
        recorder._chunk_energies.append(0.0)

    result = recorder.drain_to_silence(min_silence_ms=600)
    assert result is not None, (
        "drain_to_silence with 6 silent chunks at 1700 samples SHOULD drain "
        "(ceil(9600/1700)=6 chunks required, 6 provided)"
    )
    recorder.stop()


def test_callback_survives_unexpected_exception(
    output_path: Path, mock_sd, mock_sf
) -> None:
    """_callback must not propagate unexpected exceptions.

    Regression: an unguarded exception inside _callback would kill the
    PortAudio audio thread, silently stopping all audio capture.
    """
    _, mock_file = mock_sf
    recorder = AudioRecorder(output_path)
    recorder.start()

    # Force the sound file write to raise an unexpected non-OSError exception
    mock_file.write.side_effect = ValueError("unexpected numpy shape mismatch")

    indata = np.zeros((1024, 1), dtype="int16")
    # Must not raise — the safety net should catch it
    try:
        recorder._callback(indata, 1024, None, None)
    except Exception as exc:
        pytest.fail(f"_callback propagated an exception: {exc!r}")

    # Recorder state must remain intact (still recording, not paused)
    assert recorder.is_recording
    assert not recorder.is_paused
    recorder.stop()
