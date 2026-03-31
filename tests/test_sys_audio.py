"""Unit tests for SystemAudioCapture and find_blackhole_device."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

# ---------------------------------------------------------------------------
# find_blackhole_device
# ---------------------------------------------------------------------------


def _make_device(name: str, max_input_channels: int) -> dict:
    return {"name": name, "max_input_channels": max_input_channels}


def test_find_blackhole_not_found() -> None:
    """find_blackhole_device returns None when no BlackHole device exists."""
    devices = [
        _make_device("Built-in Microphone", 1),
        _make_device("USB Audio", 2),
    ]
    mock_sd = MagicMock()
    mock_sd.query_devices.return_value = devices
    with patch.dict("sys.modules", {"sounddevice": mock_sd}):
        from scarecrow.sys_audio import find_blackhole_device

        result = find_blackhole_device()
    assert result is None


def test_find_blackhole_found() -> None:
    """find_blackhole_device returns the correct index for 'BlackHole 2ch'."""
    devices = [
        _make_device("Built-in Microphone", 1),
        _make_device("BlackHole 2ch", 2),
        _make_device("USB Audio", 2),
    ]
    mock_sd = MagicMock()
    mock_sd.query_devices.return_value = devices
    with patch.dict("sys.modules", {"sounddevice": mock_sd}):
        from scarecrow.sys_audio import find_blackhole_device

        result = find_blackhole_device()
    assert result == 1


def test_find_blackhole_case_insensitive() -> None:
    """find_blackhole_device matches 'blackhole' (lowercase) against 'BlackHole 2ch'."""
    devices = [
        _make_device("Built-in Microphone", 1),
        _make_device("BlackHole 2ch", 2),
    ]
    mock_sd = MagicMock()
    mock_sd.query_devices.return_value = devices
    with patch.dict("sys.modules", {"sounddevice": mock_sd}):
        from scarecrow.sys_audio import find_blackhole_device

        result = find_blackhole_device(name="blackhole")
    assert result == 1


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def output_path(tmp_path: Path) -> Path:
    return tmp_path / "audio_sys.wav"


@pytest.fixture()
def mock_sd():
    """Patch sounddevice so no real audio hardware is touched."""
    mock = MagicMock()
    mock_stream = MagicMock()
    mock.InputStream.return_value = mock_stream

    # query_devices(device) — returns device info dict
    dev_info = {"default_samplerate": 48000.0, "max_input_channels": 2}
    mock.query_devices.return_value = dev_info

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


# ---------------------------------------------------------------------------
# SystemAudioCapture — start / stop
# ---------------------------------------------------------------------------


def test_sys_capture_start_stop(output_path: Path, mock_sd, mock_sf) -> None:
    """start() opens InputStream + SoundFile with correct params; stop() tears down."""
    from scarecrow.sys_audio import SystemAudioCapture

    mock_sd_module, mock_stream = mock_sd
    mock_sf_module, mock_file = mock_sf

    capture = SystemAudioCapture(output_path, device=3)
    capture.start()

    # SoundFile opened with sample rate and channels from query_devices
    mock_sf_module.SoundFile.assert_called_once_with(
        output_path,
        mode="w",
        samplerate=48000,
        channels=2,
        subtype="PCM_16",
    )

    # InputStream opened with device and matching sample rate / channels
    call_kwargs = mock_sd_module.InputStream.call_args.kwargs
    assert call_kwargs["device"] == 3
    assert call_kwargs["samplerate"] == 48000
    assert call_kwargs["channels"] == 2

    # Stream was started
    mock_stream.start.assert_called_once()

    capture.stop()

    # Stream was stopped and closed
    mock_stream.stop.assert_called_once()
    mock_stream.close.assert_called_once()

    # SoundFile closed (either by writer thread or safety net in stop)
    mock_file.close.assert_called()


# ---------------------------------------------------------------------------
# Peak level decay
# ---------------------------------------------------------------------------


def test_sys_capture_peak_level_decays(output_path: Path, mock_sd, mock_sf) -> None:
    """peak_level decays by _peak_decay on each read."""
    from scarecrow.sys_audio import SystemAudioCapture

    capture = SystemAudioCapture(output_path, device=0)
    capture.start()

    # Inject a max-amplitude chunk to push _held_peak to 1.0
    indata = np.full((1024, 2), 32767, dtype="int16")
    capture._callback(indata, 1024, None, None)

    first_read = capture.peak_level
    second_read = capture.peak_level

    assert first_read == pytest.approx(1.0, abs=1e-4)
    assert second_read == pytest.approx(first_read - capture._peak_decay, abs=1e-6)

    capture.stop()


# ---------------------------------------------------------------------------
# Write queue
# ---------------------------------------------------------------------------


def test_sys_capture_writes_wav(output_path: Path, mock_sd, mock_sf) -> None:
    """Callback enqueues audio data so the writer thread can write it to file."""
    _, mock_file = mock_sf
    from scarecrow.sys_audio import SystemAudioCapture

    capture = SystemAudioCapture(output_path, device=0)
    capture.start()

    indata = np.zeros((1024, 2), dtype="int16")
    capture._callback(indata, 1024, None, None)

    # stop() joins the writer thread, so all queued data must have been written
    capture.stop()

    assert mock_file.write.call_count >= 1
    written = mock_file.write.call_args[0][0]
    assert written.shape == (1024, 2)


# ---------------------------------------------------------------------------
# Pause / resume
# ---------------------------------------------------------------------------


def test_sys_capture_pause_resume(output_path: Path, mock_sd, mock_sf) -> None:
    """pause() stops the stream; resume() starts it again."""
    from scarecrow.sys_audio import SystemAudioCapture

    _, mock_stream = mock_sd

    capture = SystemAudioCapture(output_path, device=0)
    capture.start()
    assert mock_stream.start.call_count == 1

    capture.pause()
    assert capture._paused is True
    assert mock_stream.stop.call_count == 1

    capture.resume()
    assert capture._paused is False
    assert mock_stream.start.call_count == 2

    capture.stop()


# ---------------------------------------------------------------------------
# Callback ignored when not recording
# ---------------------------------------------------------------------------


def test_sys_capture_callback_ignores_when_not_recording(
    output_path: Path, mock_sd, mock_sf
) -> None:
    """Callback must not enqueue data when _recording is False."""
    from scarecrow.sys_audio import SystemAudioCapture

    capture = SystemAudioCapture(output_path, device=0)
    capture.start()

    # Force _recording off without going through stop() teardown
    capture._recording = False

    indata = np.ones((1024, 2), dtype="int16") * 500
    capture._callback(indata, 1024, None, None)

    assert capture._write_queue.empty()

    # Clean up — stop() is safe to call even after manually clearing _recording
    capture.stop()


# ---------------------------------------------------------------------------
# Session.compress_sys_audio
# ---------------------------------------------------------------------------


def test_compress_sys_audio_streaming(tmp_path: Path) -> None:
    """compress_sys_audio converts audio_sys.wav → audio_sys.flac and deletes WAV."""
    import numpy as np
    import soundfile as sf

    from scarecrow.session import Session

    session = Session(base_dir=tmp_path)

    # Write a real 2-channel WAV at 48kHz
    audio = np.zeros((48000, 2), dtype=np.float32)  # 1 second stereo silence
    sf.write(session.audio_sys_path, audio, 48000)
    assert session.audio_sys_path.exists()

    result = session.compress_sys_audio()

    assert result is not None
    assert result.suffix == ".flac"
    assert result.exists()
    assert not session.audio_sys_path.exists()  # WAV deleted
    session.finalize()


def test_compress_sys_audio_no_file(tmp_path: Path) -> None:
    """compress_sys_audio returns None when audio_sys.wav does not exist."""
    from scarecrow.session import Session

    session = Session(base_dir=tmp_path)
    assert not session.audio_sys_path.exists()

    result = session.compress_sys_audio()

    assert result is None
    session.finalize()


def test_compress_sys_audio_independent_of_mic(tmp_path: Path) -> None:
    """compress_sys_audio succeeds even when audio.wav (mic) is absent."""
    import numpy as np
    import soundfile as sf

    from scarecrow.session import Session

    session = Session(base_dir=tmp_path)

    # Only create the sys audio WAV — no mic WAV
    audio = np.zeros((48000, 2), dtype=np.float32)
    sf.write(session.audio_sys_path, audio, 48000)
    assert not session.audio_path.exists()

    # compress_audio (mic) returns None — sys audio should still compress fine
    mic_result = session.compress_audio()
    assert mic_result is None

    sys_result = session.compress_sys_audio()
    assert sys_result is not None
    assert sys_result.suffix == ".flac"
    session.finalize()
