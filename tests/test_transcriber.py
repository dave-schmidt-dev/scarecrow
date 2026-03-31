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

    with (
        patch.object(t, "_transcribe_parakeet", side_effect=RuntimeError("boom")),
        patch("scarecrow.transcriber.time.sleep"),
    ):
        t.transcribe_batch(np.zeros(16000, dtype=np.float32), batch_elapsed=30)

    assert errors == [
        ("batch", "Batch transcription failed after retries. Audio is still recording.")
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

    mock_parakeet = MagicMock()
    mock_parakeet.from_pretrained = counting_from_pretrained
    with patch.dict("sys.modules", {"parakeet_mlx": mock_parakeet}):
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


def test_transcribe_batch_retries_on_failure() -> None:
    """transcribe_batch must retry up to 3 times before giving up."""
    call_count = 0

    def failing_then_success(audio):
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise RuntimeError("temporary failure")
        return "recovered text"

    t = Transcriber()
    t._ready = True
    t._model_manager = MagicMock()

    with (
        patch.object(t, "_transcribe_parakeet", side_effect=failing_then_success),
        patch("scarecrow.transcriber.time.sleep"),
    ):
        result = t.transcribe_batch(
            np.zeros(16000, dtype=np.float32), 30, emit_callback=False
        )

    assert result == "recovered text"
    assert call_count == 3
    assert t.consecutive_failures == 0


def test_transcribe_batch_exhausts_retries() -> None:
    """After all retries fail, transcribe_batch returns None and increments failures."""
    errors: list[tuple[str, str]] = []

    t = Transcriber(
        TranscriberBindings(on_error=lambda source, msg: errors.append((source, msg)))
    )
    t._ready = True
    t._model_manager = MagicMock()

    with (
        patch.object(t, "_transcribe_parakeet", side_effect=RuntimeError("permanent")),
        patch("scarecrow.transcriber.time.sleep"),
    ):
        result = t.transcribe_batch(
            np.zeros(16000, dtype=np.float32), 30, emit_callback=False
        )

    assert result is None
    assert t.consecutive_failures == 1
    assert len(errors) == 1
    assert "after retries" in errors[0][1]


def test_transcribe_batch_resets_failures_on_success() -> None:
    """A successful transcription must reset the consecutive failure count."""
    t = Transcriber()
    t._ready = True
    t._model_manager = MagicMock()
    t._consecutive_failures = 5

    with patch.object(t, "_transcribe_parakeet", return_value="hello"):
        t.transcribe_batch(np.zeros(16000, dtype=np.float32), 30, emit_callback=False)

    assert t.consecutive_failures == 0


def test_transcribe_batch_source_sys_dispatches_to_sys_callback() -> None:
    """source='sys' must dispatch to on_sys_batch_result, not on_batch_result."""
    mic_results: list[tuple[str, int]] = []
    sys_results: list[tuple[str, int]] = []

    t = Transcriber(
        TranscriberBindings(
            on_batch_result=lambda text, elapsed: mic_results.append((text, elapsed)),
            on_sys_batch_result=lambda text, elapsed: sys_results.append(
                (text, elapsed)
            ),
        )
    )
    t._ready = True
    t._model_manager = MagicMock()

    with patch.object(t, "_transcribe_parakeet", return_value="system audio text"):
        text = t.transcribe_batch(
            np.zeros(16000, dtype=np.float32),
            batch_elapsed=55,
            source="sys",
        )

    assert text == "system audio text"
    assert sys_results == [("system audio text", 55)]
    assert mic_results == []


def test_transcribe_batch_source_mic_dispatches_to_mic_callback() -> None:
    """source='mic' (default) dispatches to on_batch_result, not on_sys_batch_result."""
    mic_results: list[tuple[str, int]] = []
    sys_results: list[tuple[str, int]] = []

    t = Transcriber(
        TranscriberBindings(
            on_batch_result=lambda text, elapsed: mic_results.append((text, elapsed)),
            on_sys_batch_result=lambda text, elapsed: sys_results.append(
                (text, elapsed)
            ),
        )
    )
    t._ready = True
    t._model_manager = MagicMock()

    with patch.object(t, "_transcribe_parakeet", return_value="mic audio text"):
        text = t.transcribe_batch(
            np.zeros(16000, dtype=np.float32),
            batch_elapsed=60,
            source="mic",
        )

    assert text == "mic audio text"
    assert mic_results == [("mic audio text", 60)]
    assert sys_results == []


def test_transcribe_batch_source_sys_falls_back_to_mic_callback_when_unbound() -> None:
    """source='sys' with no sys callback falls back to on_batch_result (elif branch)."""
    mic_results: list[tuple[str, int]] = []

    t = Transcriber(
        TranscriberBindings(
            on_batch_result=lambda text, elapsed: mic_results.append((text, elapsed)),
            on_sys_batch_result=None,
        )
    )
    t._ready = True
    t._model_manager = MagicMock()

    with patch.object(t, "_transcribe_parakeet", return_value="sys text"):
        text = t.transcribe_batch(
            np.zeros(16000, dtype=np.float32),
            batch_elapsed=70,
            source="sys",
        )

    assert text == "sys text"
    # on_sys_batch_result is None → elif branch fires on_batch_result instead
    assert mic_results == [("sys text", 70)]


def test_transcribe_batch_respects_max_retries_zero() -> None:
    """When max_retries=0, transcribe_batch must attempt exactly once and not retry."""
    call_count = 0

    def always_fail(audio):
        nonlocal call_count
        call_count += 1
        raise RuntimeError("permanent failure")

    t = Transcriber()
    t._ready = True
    t._model_manager = MagicMock()

    with (
        patch.object(t, "_transcribe_parakeet", side_effect=always_fail),
        patch("scarecrow.transcriber.time.sleep") as mock_sleep,
    ):
        result = t.transcribe_batch(
            np.zeros(16000, dtype=np.float32),
            30,
            emit_callback=False,
            max_retries=0,
        )

    assert result is None
    assert call_count == 1, (
        f"Expected exactly 1 attempt with max_retries=0, got {call_count}"
    )
    mock_sleep.assert_not_called()
