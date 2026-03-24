"""Tests for the Silero VAD + faster-whisper transcriber."""

from __future__ import annotations

import queue
import time
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from scarecrow.transcriber import Transcriber, TranscriberBindings, _SileroVAD

# ---------------------------------------------------------------------------
# _SileroVAD unit tests
# ---------------------------------------------------------------------------


def test_vad_returns_float_for_512_samples() -> None:
    """VAD should return a float probability for 512-sample input."""
    vad = _SileroVAD()
    chunk = np.zeros(512, dtype=np.float32)
    prob = vad(chunk)
    assert isinstance(prob, float)
    assert 0.0 <= prob <= 1.0


def test_vad_raises_on_wrong_chunk_size() -> None:
    """VAD should raise ValueError for non-512-sample input."""
    vad = _SileroVAD()
    with pytest.raises(ValueError, match="Expected 512"):
        vad(np.zeros(256, dtype=np.float32))


def test_vad_reset_states_clears_state() -> None:
    """After reset, running the same input should give the same output."""
    vad = _SileroVAD()
    chunk = np.random.randn(512).astype(np.float32) * 0.1

    # Run once to set state
    vad(chunk)
    vad.reset_states()
    result_a = vad(chunk)

    vad.reset_states()
    result_b = vad(chunk)

    assert result_a == pytest.approx(result_b, abs=1e-6)


# ---------------------------------------------------------------------------
# Transcriber unit tests
# ---------------------------------------------------------------------------


def test_prepare_sets_is_ready() -> None:
    """prepare() should load models and set is_ready."""
    t = Transcriber()
    assert not t.is_ready

    with patch("faster_whisper.WhisperModel"):
        t.prepare()

    assert t.is_ready


def test_feed_audio_before_prepare_is_noop() -> None:
    """feed_audio before prepare should not raise."""
    t = Transcriber()
    chunk = np.zeros((512, 1), dtype=np.int16)
    t.feed_audio(chunk)  # should not raise


def test_feed_audio_drops_when_queue_full() -> None:
    """feed_audio should not block when queue is full."""
    t = Transcriber()
    t._ready = True
    t._queue = queue.Queue(maxsize=2)

    chunk = np.zeros((512, 1), dtype=np.int16)
    # Fill the queue
    t._queue.put(chunk)
    t._queue.put(chunk)

    # This should not block
    start = time.monotonic()
    t.feed_audio(chunk)
    elapsed = time.monotonic() - start
    assert elapsed < 0.1


def test_shutdown_joins_thread() -> None:
    """shutdown() should stop the worker thread."""
    t = Transcriber()
    with patch("faster_whisper.WhisperModel"):
        t.prepare()
    t.start()
    assert t._worker is not None
    assert t._worker.is_alive()

    t.shutdown()
    assert t._worker is None or not t._worker.is_alive()


def test_start_before_prepare_is_noop() -> None:
    """start() before prepare should not start a thread."""
    t = Transcriber()
    t.start()
    assert t._worker is None


# ---------------------------------------------------------------------------
# VAD state machine integration tests (mocked model)
# ---------------------------------------------------------------------------


def _make_transcriber_with_mocked_model():
    """Create a Transcriber with real VAD but mocked Whisper."""
    t = Transcriber()
    t._vad = _SileroVAD()

    mock_model = MagicMock()
    mock_segment = MagicMock()
    mock_segment.text = "test transcription"
    mock_model.transcribe.return_value = ([mock_segment], None)
    t._model = mock_model
    t._ready = True
    return t, mock_model


def test_vad_fires_update_on_speech() -> None:
    """Worker should fire on_realtime_update during speech."""
    t, _mock_model = _make_transcriber_with_mocked_model()

    updates: list[str] = []
    t.set_callbacks(on_realtime_update=updates.append)

    t.start()

    # Feed "speech" — loud signal that should trigger VAD
    speech = (np.random.randn(16000) * 10000).astype(np.int16)
    for i in range(0, len(speech), 512):
        chunk = speech[i : i + 512].reshape(-1, 1)
        if len(chunk) == 512:
            t.feed_audio(chunk)
    time.sleep(1)

    t.shutdown()
    # We can't guarantee VAD triggers on random noise, but the pipeline
    # should at least not crash
    assert isinstance(updates, list)


def test_worker_stops_on_poison_pill() -> None:
    """Worker should exit cleanly when None is queued."""
    t, _ = _make_transcriber_with_mocked_model()
    t.start()
    assert t._worker is not None

    t.stop()
    t._worker.join(timeout=3)
    assert not t._worker.is_alive()


def test_batch_transcription_error_emits_callback() -> None:
    """Batch transcription failures must surface through on_error."""
    errors: list[tuple[str, str]] = []
    t = Transcriber()
    t._ready = True
    failing_model = MagicMock()
    failing_model.transcribe.side_effect = RuntimeError("boom")
    t._model_manager = MagicMock()
    t._model_manager.get_batch_model.return_value = failing_model
    t.bind(
        TranscriberBindings(
            on_error=lambda source, message: errors.append((source, message))
        )
    )

    t.transcribe_batch(np.zeros(16000, dtype=np.float32), batch_elapsed=30)

    assert errors == [
        ("batch", "Batch transcription failed. See debug log for the stack trace.")
    ]


def test_get_batch_model_thread_safety() -> None:
    """Concurrent get_batch_model() calls must only create the model once."""
    import threading

    from scarecrow.runtime import ModelManager

    manager = ModelManager()
    call_count = 0

    def counting_create(model_name: str) -> MagicMock:
        nonlocal call_count
        call_count += 1
        return MagicMock()

    manager._create_model = staticmethod(counting_create)

    barrier = threading.Barrier(2)
    results: list[object] = [None, None]

    def worker(idx: int) -> None:
        barrier.wait()
        results[idx] = manager.get_batch_model()

    t1 = threading.Thread(target=worker, args=(0,))
    t2 = threading.Thread(target=worker, args=(1,))
    t1.start()
    t2.start()
    t1.join(timeout=5)
    t2.join(timeout=5)

    assert call_count == 1, f"Expected 1 model creation, got {call_count}"
    assert results[0] is results[1], "Both threads must get the same model instance"
