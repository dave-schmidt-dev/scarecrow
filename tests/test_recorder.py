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
    with patch("scarecrow.recorder.sd") as mock:
        mock_stream = MagicMock()
        mock.InputStream.return_value = mock_stream
        yield mock, mock_stream


@pytest.fixture()
def mock_sf():
    """Patch soundfile so no real files are written."""
    with patch("scarecrow.recorder.sf") as mock:
        mock_file = MagicMock()
        mock.SoundFile.return_value = mock_file
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


def test_on_audio_exception_does_not_crash_callback(
    output_path: Path, mock_sd, mock_sf
) -> None:
    """_callback() continues to write audio even when _on_audio raises."""
    _, mock_file = mock_sf

    def bad_callback(data: np.ndarray) -> None:
        raise RuntimeError("simulated consumer crash")

    recorder = AudioRecorder(output_path, on_audio=bad_callback)
    recorder.start()

    indata = np.zeros((1024, 1), dtype="int16")
    recorder._callback(indata, 1024, None, None)
    recorder._callback(indata, 1024, None, None)

    assert mock_file.write.call_count == 2
    recorder.stop()


def test_peak_level_returns_correct_value(output_path: Path, mock_sd, mock_sf) -> None:
    """peak_level property returns the value set by the callback, under lock."""
    recorder = AudioRecorder(output_path)
    recorder.start()

    indata = np.full((1024, 1), 32767, dtype="int16")
    recorder._callback(indata, 1024, None, None)

    assert recorder.peak_level == pytest.approx(32767 / 32768.0)
    recorder.stop()
