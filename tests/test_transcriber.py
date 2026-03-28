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
    """prepare() should set is_ready."""
    t = Transcriber()
    assert not t.is_ready

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
    t._model_manager = MagicMock()
    t.bind(
        TranscriberBindings(
            on_error=lambda source, message: errors.append((source, message))
        )
    )

    with patch.object(t, "_transcribe_parakeet", side_effect=RuntimeError("boom")):
        t.transcribe_batch(np.zeros(16000, dtype=np.float32), batch_elapsed=30)

    assert errors == [
        ("batch", "Batch transcription failed. See debug log for the stack trace.")
    ]


def test_transcribe_batch_returns_text_and_emits_callback_once() -> None:
    """The normal batch path should return text and emit one callback."""
    results: list[tuple[str, int]] = []

    t = Transcriber(
        TranscriberBindings(
            on_batch_result=lambda text, elapsed: results.append((text, elapsed))
        )
    )
    t._ready = True
    t._model_manager = MagicMock()

    with patch.object(t, "_transcribe_parakeet", return_value="hello batch"):
        text = t.transcribe_batch(np.zeros(16000, dtype=np.float32), batch_elapsed=30)

    assert text == "hello batch"
    assert results == [("hello batch", 30)]


def test_transcribe_batch_can_skip_callback_for_synchronous_flush() -> None:
    """The synchronous shutdown flush must be able to bypass the async callback."""
    results: list[tuple[str, int]] = []

    t = Transcriber(
        TranscriberBindings(
            on_batch_result=lambda text, elapsed: results.append((text, elapsed))
        )
    )
    t._ready = True
    t._model_manager = MagicMock()

    with patch.object(t, "_transcribe_parakeet", return_value="final flush"):
        text = t.transcribe_batch(
            np.zeros(16000, dtype=np.float32),
            batch_elapsed=30,
            emit_callback=False,
        )

    assert text == "final flush"
    assert results == []


def test_get_parakeet_model_thread_safety() -> None:
    """Concurrent get_parakeet_model() calls must only create the model once."""
    from scarecrow.runtime import ModelManager

    manager = ModelManager()
    call_count = 0

    mock_model = MagicMock()

    def counting_from_pretrained(model_name: str) -> MagicMock:
        nonlocal call_count
        call_count += 1
        return mock_model

    barrier = threading.Barrier(2)
    results: list[object] = [None, None]

    def worker(idx: int) -> None:
        barrier.wait()
        results[idx] = manager.get_parakeet_model()

    with patch("parakeet_mlx.from_pretrained", side_effect=counting_from_pretrained):
        t1 = threading.Thread(target=worker, args=(0,))
        t2 = threading.Thread(target=worker, args=(1,))
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

    assert call_count == 1, f"Expected 1 model creation, got {call_count}"
    assert results[0] is results[1], "Both threads must get the same model instance"


def test_preload_batch_model_calls_get_parakeet_model() -> None:
    """preload_batch_model() calls get_parakeet_model() to warm cache."""
    t = Transcriber()
    t._model_manager = MagicMock()

    t.preload_batch_model()

    t._model_manager.get_parakeet_model.assert_called_once()


def test_preload_batch_model_before_prepare_still_invokes_manager() -> None:
    """preload_batch_model() delegates to model_manager regardless of _ready state."""
    t = Transcriber()
    t._ready = False
    t._model_manager = MagicMock()

    t.preload_batch_model()

    t._model_manager.get_parakeet_model.assert_called_once()


def test_transcribe_batch_concurrent_calls_dont_crash() -> None:
    """Concurrent batch calls must complete without errors."""
    results: list[str | None] = []
    release = threading.Event()

    def slow_transcribe(audio):
        release.wait(timeout=1)
        return "done"

    t = Transcriber()
    t._ready = True
    t._model_manager = MagicMock()

    with patch.object(t, "_transcribe_parakeet", side_effect=slow_transcribe):
        threads = [
            threading.Thread(
                target=lambda: results.append(
                    t.transcribe_batch(
                        np.zeros(16000, dtype=np.float32), 30, emit_callback=False
                    )
                ),
            )
            for _ in range(2)
        ]
        for thread in threads:
            thread.start()
        time.sleep(0.05)
        release.set()
        for thread in threads:
            thread.join(timeout=2)

    assert len(results) == 2
    assert all(r == "done" for r in results)
