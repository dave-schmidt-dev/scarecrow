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
    """stop() must close both the stream and the sound file."""
    _, mock_stream = mock_sd
    _, mock_file = mock_sf
    recorder = AudioRecorder(output_path)
    recorder.start()
    recorder.stop()
    mock_stream.stop.assert_called_once()
    mock_stream.close.assert_called_once()
    mock_file.close.assert_called()


def test_callback_writes_audio_when_recording(
    output_path: Path, mock_sd, mock_sf
) -> None:
    """Callback must enqueue audio data which the writer thread writes to file."""
    _, mock_file = mock_sf
    recorder = AudioRecorder(output_path)
    recorder.start()

    indata = np.zeros((1024, 1), dtype="int16")
    recorder._callback(indata, 1024, None, None)

    # stop() joins the writer thread, ensuring all queued data is written
    recorder.stop()
    assert mock_file.write.call_count >= 1
    written = mock_file.write.call_args[0][0]
    assert written.shape == (1024, 1)


def test_callback_writes_silence_when_paused(
    output_path: Path, mock_sd, mock_sf
) -> None:
    """Callback must enqueue silence (zeros) when paused, written to file."""
    _, mock_file = mock_sf
    recorder = AudioRecorder(output_path)
    recorder.start()
    recorder.pause()

    indata = np.ones((1024, 1), dtype="int16") * 1000
    recorder._callback(indata, 1024, None, None)

    # stop() joins the writer thread, ensuring silence was flushed to file
    recorder.stop()
    assert mock_file.write.call_count >= 1
    written = mock_file.write.call_args[0][0]
    assert np.all(written == 0)


def test_callback_does_nothing_when_stopped(
    output_path: Path, mock_sd, mock_sf
) -> None:
    """Callback must not enqueue after stop() is called."""
    recorder = AudioRecorder(output_path)
    recorder.start()
    recorder.stop()

    indata = np.zeros((1024, 1), dtype="int16")
    recorder._callback(indata, 1024, None, None)

    assert recorder._write_queue.empty()


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
    from scarecrow.config import Config

    cfg = Config(
        SAMPLE_RATE=16000,
        RECORDING_SAMPLE_RATE=16000,
        VAD_MIN_SILENCE_MS=750,
    )
    recorder = AudioRecorder(output_path, sample_rate=16000, cfg=cfg)
    recorder.start()

    # Inject loud chunks followed by silent ones.
    # VAD_MIN_SILENCE_MS=750ms; at 16000 Hz with 1600-sample chunks (0.1s each),
    # min_silent_chunks = ceil(750ms / 100ms) = 8.  We need 7 loud + 8 silent
    # so the silence boundary is found mid-buffer (silence_end > 0).
    loud_chunk = np.full((1600, 1), 16000, dtype="int16")
    silent_chunk = np.zeros((1600, 1), dtype="int16")

    with recorder._buffer_lock:
        for _ in range(7):
            recorder._audio_chunks.append(loud_chunk.copy())
            recorder._chunk_energies.append(0.5)  # loud — well above threshold
        for _ in range(8):
            recorder._audio_chunks.append(silent_chunk.copy())
            recorder._chunk_energies.append(0.0)  # silent — below threshold

    result = recorder.drain_to_silence()

    # Should have drained something (loud portion up to or through silence)
    assert result is not None
    audio, energies = result
    assert len(audio) > 0
    assert isinstance(energies, list)
    recorder.stop()


def test_buffer_seconds_reflects_buffered_audio(
    output_path: Path, mock_sd, mock_sf
) -> None:
    """buffer_seconds must return the correct duration based on buffered audio."""
    from scarecrow.config import Config

    cfg = Config(SAMPLE_RATE=16000, RECORDING_SAMPLE_RATE=16000)
    recorder = AudioRecorder(output_path, sample_rate=16000, cfg=cfg)
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
    from scarecrow.config import Config

    cfg = Config(SAMPLE_RATE=16000, RECORDING_SAMPLE_RATE=16000)
    recorder = AudioRecorder(output_path, sample_rate=16000, cfg=cfg)
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
    """Callback must not crash on unexpected errors."""
    import threading

    _, mock_file = mock_sf
    recorder = AudioRecorder(output_path)

    # Block the writer thread so we can fill the queue without it draining
    block = threading.Event()
    release = threading.Event()

    def slow_write(data):
        release.set()
        block.wait()

    mock_file.write.side_effect = slow_write
    recorder.start()

    # Inject one item to engage the writer thread and then block it inside
    # slow_write — at this point the writer has dequeued that 1 item, so the
    # queue is empty and we can fill all WRITER_QUEUE_SIZE slots.
    recorder._write_queue.put(("audio", np.zeros((1, 1), dtype="int16")))
    release.wait(timeout=2)  # wait until writer is blocked inside slow_write

    # Fill queue to capacity (writer is blocked, won't drain these)
    import scarecrow.config as cfg

    for _ in range(cfg.WRITER_QUEUE_SIZE):
        recorder._write_queue.put(("audio", np.zeros((1, 1), dtype="int16")))

    indata = np.ones((1024, 1), dtype="int16") * 1000
    # Should not crash even with queue full
    recorder._callback(indata, 1024, None, None)

    # Audio is dropped but transcription buffer still gets it
    assert len(recorder._audio_chunks) == 1

    # Unblock writer so stop() can complete
    block.set()
    mock_file.write.side_effect = None
    recorder.stop()


def test_writer_thread_flushes_on_stop(output_path: Path, mock_sd, mock_sf) -> None:
    """Writer thread must write all queued audio before stop() returns."""
    _, mock_file = mock_sf
    recorder = AudioRecorder(output_path)
    recorder.start()

    # Enqueue several chunks via the callback
    for _ in range(5):
        indata = np.ones((1024, 1), dtype="int16") * 500
        recorder._callback(indata, 1024, None, None)

    recorder.stop()

    # Writer thread should have written all 5 chunks
    assert mock_file.write.call_count == 5


def test_writer_thread_handles_disk_error(output_path: Path, mock_sd, mock_sf) -> None:
    """Writer thread must set _disk_write_failed on OSError."""
    _, mock_file = mock_sf
    mock_file.write.side_effect = OSError("disk full")
    recorder = AudioRecorder(output_path)
    recorder.start()

    indata = np.ones((1024, 1), dtype="int16") * 500
    recorder._callback(indata, 1024, None, None)

    recorder.stop()

    assert recorder._disk_write_failed is True


def test_full_queue_sets_warning(output_path: Path, mock_sd, mock_sf) -> None:
    """A full write queue must set _disk_write_failed and a warning."""
    import threading

    _, mock_file = mock_sf
    recorder = AudioRecorder(output_path)

    # Block the writer thread so the queue stays full during the callback
    block = threading.Event()
    release = threading.Event()

    def slow_write(data):
        release.set()
        block.wait()

    mock_file.write.side_effect = slow_write
    recorder.start()

    # Inject one item to engage the writer, then wait for it to block inside
    # slow_write — at this point the writer has dequeued that 1 item, so the
    # queue is empty and we can fill all WRITER_QUEUE_SIZE slots.
    recorder._write_queue.put(("audio", np.zeros((1, 1), dtype="int16")))
    release.wait(timeout=2)

    import scarecrow.config as cfg

    # Fill queue to capacity (writer is blocked, won't drain these)
    for _ in range(cfg.WRITER_QUEUE_SIZE):
        recorder._write_queue.put(("audio", np.zeros((1, 1), dtype="int16")))

    # Next callback should detect full queue
    indata = np.ones((1024, 1), dtype="int16") * 500
    recorder._callback(indata, 1024, None, None)

    assert recorder._disk_write_failed is True
    assert "queue full" in (recorder._last_warning or "").lower()

    # Unblock writer so stop() can complete
    block.set()
    mock_file.write.side_effect = None
    recorder.stop()


def test_stop_without_start_is_safe(output_path: Path, mock_sd, mock_sf) -> None:
    """stop() without start() must not crash."""
    recorder = AudioRecorder(output_path)
    recorder.stop()  # Should not raise


# ---------------------------------------------------------------------------
# Bug: batch transcription gets 44100Hz audio but Parakeet expects 16000Hz
# ---------------------------------------------------------------------------


def test_drain_buffer_returns_float32(tmp_path: Path) -> None:
    """drain_buffer must return float32 audio normalized to [-1, 1]."""
    from scarecrow.recorder import AudioRecorder

    with patch.dict(
        "sys.modules", {"sounddevice": MagicMock(), "soundfile": MagicMock()}
    ):
        recorder = AudioRecorder(tmp_path / "audio.wav", sample_rate=44100)
        recorder.start()

        # Simulate audio callback with int16 data
        indata = (np.random.randn(1024, 1) * 10000).astype("int16")
        recorder._callback(indata, 1024, None, None)

        audio = recorder.drain_buffer()
        assert audio is not None
        assert audio.dtype == np.float32
        assert audio.max() <= 1.0
        assert audio.min() >= -1.0
        recorder.stop()


def test_drain_buffer_resamples_to_stt_rate(tmp_path: Path) -> None:
    """drain_buffer downsamples 48kHz recording audio to 16kHz for STT."""
    from scarecrow.config import Config
    from scarecrow.recorder import AudioRecorder

    cfg = Config(SAMPLE_RATE=16000, RECORDING_SAMPLE_RATE=48000)

    with patch.dict(
        "sys.modules", {"sounddevice": MagicMock(), "soundfile": MagicMock()}
    ):
        recorder = AudioRecorder(tmp_path / "audio.wav", sample_rate=48000, cfg=cfg)
        recorder.start()

        # Simulate 1 second of audio at 48000Hz: 48 chunks of 1024 = 49152 samples
        for _ in range(48):
            indata = np.zeros((1024, 1), dtype="int16")
            recorder._callback(indata, 1024, None, None)

        audio = recorder.drain_buffer()
        assert audio is not None
        # Output should be ~1/3 of input length (16kHz vs 48kHz)
        assert len(audio) == pytest.approx(49152 // 3, rel=0.05)
        recorder.stop()


def test_finalize_audio_no_resample_when_rates_match(tmp_path: Path) -> None:
    """drain_buffer output length is unchanged when recording and STT rates match."""
    from scarecrow.config import Config
    from scarecrow.recorder import AudioRecorder

    cfg = Config(SAMPLE_RATE=16000, RECORDING_SAMPLE_RATE=16000)

    with patch.dict(
        "sys.modules", {"sounddevice": MagicMock(), "soundfile": MagicMock()}
    ):
        recorder = AudioRecorder(tmp_path / "audio.wav", sample_rate=16000, cfg=cfg)
        recorder.start()

        for _ in range(16):
            indata = np.zeros((1024, 1), dtype="int16")
            recorder._callback(indata, 1024, None, None)

        audio = recorder.drain_buffer()
        assert audio is not None
        assert len(audio) == 16 * 1024
        recorder.stop()


# ---------------------------------------------------------------------------
# Bug: audio buffer not populated (callback doesn't buffer when paused)
# ---------------------------------------------------------------------------


def test_callback_does_not_buffer_when_paused(tmp_path: Path) -> None:
    """Paused callback should NOT accumulate audio in the batch buffer."""
    from scarecrow.recorder import AudioRecorder

    with patch.dict(
        "sys.modules", {"sounddevice": MagicMock(), "soundfile": MagicMock()}
    ):
        recorder = AudioRecorder(tmp_path / "audio.wav")
        recorder.start()
        recorder.pause()

        indata = np.zeros((1024, 1), dtype="int16")
        recorder._callback(indata, 1024, None, None)

        audio = recorder.drain_buffer()
        assert audio is None


def test_callback_buffers_when_recording(tmp_path: Path) -> None:
    """Recording callback must accumulate audio in the batch buffer."""
    from scarecrow.config import Config
    from scarecrow.recorder import AudioRecorder

    cfg = Config(SAMPLE_RATE=16000, RECORDING_SAMPLE_RATE=16000)
    with patch.dict(
        "sys.modules", {"sounddevice": MagicMock(), "soundfile": MagicMock()}
    ):
        recorder = AudioRecorder(tmp_path / "audio.wav", sample_rate=16000, cfg=cfg)
        recorder.start()

        indata = np.ones((1024, 1), dtype="int16") * 500
        recorder._callback(indata, 1024, None, None)

        audio = recorder.drain_buffer()
        assert audio is not None
        assert len(audio) == 1024


# ---------------------------------------------------------------------------
# Bug: drain_buffer empties on second call (double-drain returns nothing)
# ---------------------------------------------------------------------------


def test_drain_buffer_empties_after_drain(tmp_path: Path) -> None:
    """drain_buffer should return None on second call with no new audio."""
    from scarecrow.recorder import AudioRecorder

    with patch.dict(
        "sys.modules", {"sounddevice": MagicMock(), "soundfile": MagicMock()}
    ):
        recorder = AudioRecorder(tmp_path / "audio.wav")
        recorder.start()

        indata = np.zeros((1024, 1), dtype="int16")
        recorder._callback(indata, 1024, None, None)

        first = recorder.drain_buffer()
        assert first is not None

        second = recorder.drain_buffer()
        assert second is None
        recorder.stop()


# ---------------------------------------------------------------------------
# Audio level meter: peak_level tracks mic input
# ---------------------------------------------------------------------------


def test_peak_level_updates_on_audio(tmp_path: Path) -> None:
    """peak_level should reflect the loudest sample in the last callback."""
    from scarecrow.config import Config
    from scarecrow.recorder import AudioRecorder

    # Explicit cfg so the test is isolated from global config mutations
    # (e.g. context-menu tests leaving MIC_GAIN=2.0 on the singleton).
    cfg = Config(SAMPLE_RATE=16000, RECORDING_SAMPLE_RATE=16000, MIC_GAIN=1.0)
    with patch.dict(
        "sys.modules", {"sounddevice": MagicMock(), "soundfile": MagicMock()}
    ):
        recorder = AudioRecorder(tmp_path / "audio.wav", cfg=cfg)
        recorder.start()

        assert recorder.peak_level == 0.0

        # Simulate loud audio (half of int16 max)
        indata = np.ones((1024, 1), dtype="int16") * 16384
        recorder._callback(indata, 1024, None, None)

        assert recorder.peak_level == pytest.approx(0.5, abs=0.01)
        recorder.stop()


def test_peak_level_zero_when_paused(tmp_path: Path) -> None:
    """peak_level should be 0 when paused."""
    from scarecrow.config import Config
    from scarecrow.recorder import AudioRecorder

    cfg = Config(SAMPLE_RATE=16000, RECORDING_SAMPLE_RATE=16000, MIC_GAIN=1.0)
    with patch.dict(
        "sys.modules", {"sounddevice": MagicMock(), "soundfile": MagicMock()}
    ):
        recorder = AudioRecorder(tmp_path / "audio.wav", cfg=cfg)
        recorder.start()
        recorder.pause()

        indata = np.ones((1024, 1), dtype="int16") * 16384
        recorder._callback(indata, 1024, None, None)

        assert recorder.peak_level == 0.0
        recorder.stop()


# ---------------------------------------------------------------------------
# Bug: drain_buffer overlap caused duplicate words in batch transcripts
# The 2s overlap meant the same words appeared at end of one batch and
# start of the next. drain_buffer must clear completely.
# ---------------------------------------------------------------------------


def test_drain_buffer_clears_completely(tmp_path: Path) -> None:
    """drain_buffer must return all audio and leave buffer empty — no overlap."""
    from scarecrow.recorder import AudioRecorder

    with patch.dict(
        "sys.modules", {"sounddevice": MagicMock(), "soundfile": MagicMock()}
    ):
        recorder = AudioRecorder(tmp_path / "audio.wav", sample_rate=16000)
        recorder.start()

        # Simulate 3 seconds of audio (48 chunks of 1024 = 49152 samples)
        for _ in range(48):
            indata = np.ones((1024, 1), dtype="int16") * 100
            recorder._callback(indata, 1024, None, None)

        first = recorder.drain_buffer()
        assert first is not None
        assert len(first) == 48 * 1024

        # Second drain must be empty — no overlap retained
        second = recorder.drain_buffer()
        assert second is None
        recorder.stop()


# ---------------------------------------------------------------------------
# Pre-gain FLAC invariant: disk=pre-gain, buffer=post-gain, RMS=pre-gain
# ---------------------------------------------------------------------------


def test_callback_disk_pregain_buffer_postgain(tmp_path: Path) -> None:
    """Three-way invariant when MIC_GAIN != 1.0:
    1. Write queue (disk) receives pre-gain audio (original samples).
    2. Transcription buffer receives post-gain audio (gain-adjusted).
    3. RMS (chunk_energies) is computed from pre-gain audio.
    """
    from scarecrow.config import Config
    from scarecrow.recorder import AudioRecorder

    cfg = Config(SAMPLE_RATE=16000, RECORDING_SAMPLE_RATE=16000, MIC_GAIN=0.5)
    mocks = {"sounddevice": MagicMock(), "soundfile": MagicMock()}
    with patch.dict("sys.modules", mocks):
        recorder = AudioRecorder(
            tmp_path / "audio.wav",
            sample_rate=16000,
            cfg=cfg,
        )
        # Bypass start() to avoid writer thread consuming queue items.
        recorder._recording = True

        indata = np.full((1024, 1), 1000, dtype=np.int16)
        recorder._callback(indata, 1024, None, None)

        # 1. Disk queue receives pre-gain audio (1000)
        tag, queued = recorder._write_queue.get_nowait()
        assert tag == "audio"
        assert queued.shape == indata.shape
        assert np.all(queued == 1000), "write queue: pre-gain"

        # 2. Transcription buffer receives post-gain (500)
        assert len(recorder._audio_chunks) == 1
        chunk = recorder._audio_chunks[0]
        assert np.all(chunk == 500), "audio_chunks: post-gain"

        # 3. RMS from pre-gain signal (1000 / 32768)
        assert len(recorder._chunk_energies) == 1
        pregain = np.full((1024, 1), 1000, dtype=np.float32)
        expected_rms = float(
            np.sqrt(np.mean((pregain / 32768.0) ** 2)),
        )
        assert recorder._chunk_energies[0] == pytest.approx(
            expected_rms,
            rel=1e-5,
        )
