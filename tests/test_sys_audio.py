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


# ---------------------------------------------------------------------------
# Transcription buffer — drain_buffer
# ---------------------------------------------------------------------------


def _make_capture(output_path: Path, mock_sd, mock_sf):
    """Helper: instantiate SystemAudioCapture without touching real hardware."""
    from scarecrow.sys_audio import SystemAudioCapture

    return SystemAudioCapture(output_path, device=0)


def test_drain_buffer_returns_float32_16khz(
    output_path: Path, mock_sd, mock_sf
) -> None:
    """drain_buffer returns float32 audio resampled to 16kHz."""
    capture = _make_capture(output_path, mock_sd, mock_sf)

    # Manually populate with int16 mono data at the device's 48kHz
    sample_rate = 48000
    n_samples = sample_rate  # 1 second of audio
    rng = np.random.default_rng(42)
    chunk = rng.integers(-1000, 1000, size=n_samples, dtype=np.int16)

    with capture._buffer_lock:
        capture._audio_chunks.append(chunk)
        capture._chunk_energies.append(0.05)

    result = capture.drain_buffer()

    assert result is not None
    assert result.dtype == np.float32
    # 1 second at 16kHz = 16000 samples (allow ±1 for rounding)
    assert abs(len(result) - 16000) <= 1


def test_drain_buffer_clears_chunks(output_path: Path, mock_sd, mock_sf) -> None:
    """drain_buffer clears _audio_chunks and _chunk_energies after returning."""
    capture = _make_capture(output_path, mock_sd, mock_sf)

    chunk = np.zeros(4800, dtype=np.int16)
    with capture._buffer_lock:
        capture._audio_chunks.append(chunk)
        capture._chunk_energies.append(0.0)

    result = capture.drain_buffer()

    assert result is not None
    assert capture._audio_chunks == []
    assert capture._chunk_energies == []


def test_drain_buffer_returns_none_when_empty(
    output_path: Path, mock_sd, mock_sf
) -> None:
    """drain_buffer returns None when there is no accumulated audio."""
    capture = _make_capture(output_path, mock_sd, mock_sf)

    result = capture.drain_buffer()

    assert result is None


# ---------------------------------------------------------------------------
# Transcription buffer — buffer_seconds
# ---------------------------------------------------------------------------


def test_buffer_seconds_tracks_accumulation(
    output_path: Path, mock_sd, mock_sf
) -> None:
    """buffer_seconds returns the correct accumulated duration in seconds."""
    capture = _make_capture(output_path, mock_sd, mock_sf)

    # Two 0.5-second chunks at 48kHz
    chunk = np.zeros(24000, dtype=np.int16)
    with capture._buffer_lock:
        capture._audio_chunks.append(chunk)
        capture._audio_chunks.append(chunk)
        capture._chunk_energies.extend([0.0, 0.0])

    assert capture.buffer_seconds == pytest.approx(1.0, abs=1e-6)


def test_buffer_seconds_zero_when_empty(output_path: Path, mock_sd, mock_sf) -> None:
    """buffer_seconds returns 0.0 when no audio has been buffered."""
    capture = _make_capture(output_path, mock_sd, mock_sf)

    assert capture.buffer_seconds == 0.0


# ---------------------------------------------------------------------------
# Transcription buffer — _callback_inner stereo downmix
# ---------------------------------------------------------------------------


def test_stereo_downmix_in_callback(output_path: Path, mock_sd, mock_sf) -> None:
    """_callback_inner downmixes stereo int16 input to a mono (1D) chunk."""
    capture = _make_capture(output_path, mock_sd, mock_sf)
    # Force _recording=True so callback doesn't bail early
    capture._recording = True

    stereo = np.ones((1024, 2), dtype=np.int16) * 500
    capture._callback_inner(stereo, status=None)

    assert len(capture._audio_chunks) == 1
    mono = capture._audio_chunks[0]
    assert mono.ndim == 1
    assert len(mono) == 1024


def test_mono_passthrough_in_callback(output_path: Path, mock_sd, mock_sf) -> None:
    """_callback_inner stores 1D chunks unchanged when input is already mono."""
    # Build a capture whose device reports 1 channel
    mock_sd_module, _ = mock_sd
    mock_sd_module.query_devices.return_value = {
        "default_samplerate": 48000.0,
        "max_input_channels": 1,
    }
    from scarecrow.sys_audio import SystemAudioCapture

    capture = SystemAudioCapture(output_path, device=0)
    capture._recording = True

    mono_in = np.ones((1024, 1), dtype=np.int16) * 300
    capture._callback_inner(mono_in, status=None)

    assert len(capture._audio_chunks) == 1
    chunk = capture._audio_chunks[0]
    assert chunk.ndim == 1
    assert len(chunk) == 1024


# ---------------------------------------------------------------------------
# Transcription buffer — RMS normalization
# ---------------------------------------------------------------------------


def test_rms_normalization_scale(output_path: Path, mock_sd, mock_sf) -> None:
    """_callback_inner stores RMS energies in the [0, 1] range."""
    capture = _make_capture(output_path, mock_sd, mock_sf)
    capture._recording = True

    # Max-amplitude stereo signal → RMS should be close to 1.0
    loud = np.full((1024, 2), 32767, dtype=np.int16)
    capture._callback_inner(loud, status=None)

    # Silent signal → RMS should be 0.0
    capture._recording = True
    silent = np.zeros((1024, 2), dtype=np.int16)
    capture._callback_inner(silent, status=None)

    assert len(capture._chunk_energies) == 2
    loud_rms, silent_rms = capture._chunk_energies
    assert 0.0 <= loud_rms <= 1.0
    assert loud_rms > 0.9
    assert silent_rms == pytest.approx(0.0, abs=1e-9)


# ---------------------------------------------------------------------------
# Transcription buffer — drain_to_silence
# ---------------------------------------------------------------------------


def test_drain_to_silence_finds_boundary(output_path: Path, mock_sd, mock_sf) -> None:
    """drain_to_silence returns audio up to the first silence boundary."""
    capture = _make_capture(output_path, mock_sd, mock_sf)

    sample_rate = 48000
    # 10 chunks of "speech" (high RMS) followed by 5 chunks of silence.
    # Each chunk = 4800 samples (100 ms at 48kHz).
    chunk_samples = 4800
    speech_chunk = np.full(chunk_samples, 8000, dtype=np.int16)
    silence_chunk = np.zeros(chunk_samples, dtype=np.int16)

    n_speech = 10
    n_silence = 5

    speech_f32 = speech_chunk.astype(np.float32) / 32768.0
    speech_rms = float(np.sqrt(np.mean(speech_f32**2)))
    silence_rms = 0.0

    with capture._buffer_lock:
        for _ in range(n_speech):
            capture._audio_chunks.append(speech_chunk.copy())
            capture._chunk_energies.append(speech_rms)
        for _ in range(n_silence):
            capture._audio_chunks.append(silence_chunk.copy())
            capture._chunk_energies.append(silence_rms)

    # min_silence_ms=400 → needs ceil(400/100) = 4 silent chunks
    result = capture.drain_to_silence(
        silence_threshold=0.01,
        min_silence_ms=400,
        max_buffer_seconds=30,
    )

    assert result is not None
    audio, energies = result
    assert audio.dtype == np.float32
    # The drained portion must not exceed total buffer length
    total_chunks = n_speech + n_silence
    total_samples_48k = total_chunks * chunk_samples
    expected_max_16k = int(total_samples_48k / sample_rate * 16000)
    assert len(audio) <= expected_max_16k + 1
    # Some audio was drained
    assert len(audio) > 0
    assert len(energies) > 0


def test_drain_to_silence_returns_none_when_too_short(
    output_path: Path, mock_sd, mock_sf
) -> None:
    """drain_to_silence returns None when buffer is under 0.5 seconds."""
    capture = _make_capture(output_path, mock_sd, mock_sf)

    # Only 0.1 seconds of audio (below the 0.5 s minimum)
    tiny = np.zeros(4800, dtype=np.int16)  # 100 ms at 48kHz
    with capture._buffer_lock:
        capture._audio_chunks.append(tiny)
        capture._chunk_energies.append(0.0)

    result = capture.drain_to_silence(silence_threshold=0.01)

    assert result is None


def test_drain_to_silence_returns_none_when_no_silence_found(
    output_path: Path, mock_sd, mock_sf
) -> None:
    """drain_to_silence returns None when buffer has no silent region yet."""
    capture = _make_capture(output_path, mock_sd, mock_sf)

    # 1 second of continuous speech — no silence boundary
    speech = np.full(48000, 8000, dtype=np.int16)
    speech_rms = float(np.sqrt(np.mean((speech.astype(np.float32) / 32768.0) ** 2)))
    with capture._buffer_lock:
        capture._audio_chunks.append(speech)
        capture._chunk_energies.append(speech_rms)

    result = capture.drain_to_silence(
        silence_threshold=0.01,
        min_silence_ms=750,
        max_buffer_seconds=30,
    )

    assert result is None


# ---------------------------------------------------------------------------
# Pre-gain FLAC invariant: disk=pre-gain, buffer=post-gain, RMS=pre-gain
# ---------------------------------------------------------------------------


def test_callback_disk_pregain_buffer_postgain(
    output_path: Path, mock_sd, mock_sf
) -> None:
    """Three-way invariant when gain != 1.0:
    1. Write queue (disk) receives pre-gain audio (original samples).
    2. Transcription buffer receives post-gain audio (gain-adjusted mono).
    3. RMS (chunk_energies) is computed from pre-gain audio.
    """
    capture = _make_capture(output_path, mock_sd, mock_sf)
    capture._recording = True
    capture._gain = 0.5

    indata = np.full((1024, 2), 1000, dtype=np.int16)
    capture._callback_inner(indata, status=None)

    # 1. Disk queue receives pre-gain audio — original sample values (1000)
    tag, queued = capture._write_queue.get_nowait()
    assert tag == "audio"
    assert queued.shape == indata.shape
    assert np.all(queued == 1000), "write queue must hold pre-gain audio"

    # 2. Transcription buffer receives post-gain mono (1000 * 0.5 = 500)
    assert len(capture._audio_chunks) == 1
    mono = capture._audio_chunks[0]
    assert mono.ndim == 1
    assert len(mono) == 1024
    assert np.all(mono == 500), "audio_chunks must hold post-gain mono audio"

    # 3. RMS is computed from pre-gain signal (1000 / 32768)
    assert len(capture._chunk_energies) == 1
    pregain = np.full(1024, 1000, dtype=np.float32)
    expected_rms = float(
        np.sqrt(np.mean((pregain / 32768.0) ** 2)),
    )
    assert capture._chunk_energies[0] == pytest.approx(expected_rms, rel=1e-5)
