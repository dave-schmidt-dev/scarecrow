"""Tests for the batch-only Transcriber."""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

import numpy as np

from scarecrow.transcriber import Transcriber, TranscriberBindings

# ---------------------------------------------------------------------------
# Transcriber unit tests
# ---------------------------------------------------------------------------


def test_prepare_sets_is_ready() -> None:
    """prepare() should load models and set is_ready."""
    t = Transcriber()
    assert not t.is_ready

    with patch("scarecrow.runtime.WhisperModel"):
        t.prepare()

    assert t.is_ready
    t.shutdown(timeout=0)


def test_shutdown_releases_runtime_references() -> None:
    """shutdown() must drop model references and mark ready=False."""
    t = Transcriber()
    t._ready = True
    t._model_manager = MagicMock()

    t.shutdown(timeout=0)

    assert not t.is_ready
    t._model_manager.release_models.assert_called_once()


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


def test_transcribe_batch_returns_text_and_emits_callback_once() -> None:
    """The normal batch path should return text and emit one callback."""
    results: list[tuple[str, int]] = []
    segment = MagicMock()
    segment.text = "hello batch"

    model = MagicMock()
    model.transcribe.return_value = ([segment], None)

    t = Transcriber(
        TranscriberBindings(
            on_batch_result=lambda text, elapsed: results.append((text, elapsed))
        )
    )
    t._ready = True
    t._model_manager = MagicMock()
    t._model_manager.get_batch_model.return_value = model

    text = t.transcribe_batch(np.zeros(16000, dtype=np.float32), batch_elapsed=30)

    assert text == "hello batch"
    assert results == [("hello batch", 30)]


def test_transcribe_batch_can_skip_callback_for_synchronous_flush() -> None:
    """The synchronous shutdown flush must be able to bypass the async callback."""
    results: list[tuple[str, int]] = []
    segment = MagicMock()
    segment.text = "final flush"

    model = MagicMock()
    model.transcribe.return_value = ([segment], None)

    t = Transcriber(
        TranscriberBindings(
            on_batch_result=lambda text, elapsed: results.append((text, elapsed))
        )
    )
    t._ready = True
    t._model_manager = MagicMock()
    t._model_manager.get_batch_model.return_value = model

    text = t.transcribe_batch(
        np.zeros(16000, dtype=np.float32),
        batch_elapsed=30,
        emit_callback=False,
    )

    assert text == "final flush"
    assert results == []


def test_get_batch_model_thread_safety() -> None:
    """Concurrent get_batch_model() calls must only create the model once."""
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


def test_transcribe_batch_passes_initial_prompt_to_model() -> None:
    """initial_prompt must be forwarded to model.transcribe when provided."""
    segment = MagicMock()
    segment.text = "Malcolm X was a civil rights leader"

    model = MagicMock()
    model.transcribe.return_value = ([segment], None)

    t = Transcriber()
    t._ready = True
    t._model_manager = MagicMock()
    t._model_manager.get_batch_model.return_value = model

    t.transcribe_batch(
        np.zeros(16000, dtype=np.float32),
        batch_elapsed=30,
        initial_prompt="Malcolm X",
    )

    _, call_kwargs = model.transcribe.call_args
    assert call_kwargs.get("initial_prompt") == "Malcolm X"


def test_transcribe_batch_omits_initial_prompt_when_none() -> None:
    """initial_prompt must not be passed to model.transcribe when None."""
    segment = MagicMock()
    segment.text = "hello"

    model = MagicMock()
    model.transcribe.return_value = ([segment], None)

    t = Transcriber()
    t._ready = True
    t._model_manager = MagicMock()
    t._model_manager.get_batch_model.return_value = model

    t.transcribe_batch(np.zeros(16000, dtype=np.float32), batch_elapsed=30)

    _, call_kwargs = model.transcribe.call_args
    assert "initial_prompt" not in call_kwargs


def test_transcribe_batch_serializes_overlapping_calls() -> None:
    """Concurrent batch requests must not run the shared batch model in parallel."""
    active = 0
    max_active = 0
    lock = threading.Lock()
    release = threading.Event()

    def blocking_transcribe(*args, **kwargs):
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
        release.wait(timeout=1)
        with lock:
            active -= 1
        segment = MagicMock()
        segment.text = "done"
        return [segment], None

    model = MagicMock()
    model.transcribe.side_effect = blocking_transcribe

    t = Transcriber()
    t._ready = True
    t._model_manager = MagicMock()
    t._model_manager.get_batch_model.return_value = model

    threads = [
        threading.Thread(
            target=t.transcribe_batch,
            args=(np.zeros(16000, dtype=np.float32), 30),
        )
        for _ in range(2)
    ]
    for thread in threads:
        thread.start()
    time.sleep(0.1)
    release.set()
    for thread in threads:
        thread.join(timeout=2)

    assert max_active == 1
