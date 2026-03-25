"""Tests for the Apple Speech live captioner."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from scarecrow.live_captioner import CaptionerBindings, LiveCaptioner

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_speech_modules():
    """Return patches that prevent actual Speech framework calls."""
    mock_speech = MagicMock()
    mock_speech.SFSpeechRecognizerAuthorizationStatusAuthorized = 0
    mock_speech.SFSpeechRecognitionTaskHintDictation = 1

    def fake_request_auth(handler):
        handler(0)  # authorized

    mock_speech.SFSpeechRecognizer.requestAuthorization_ = fake_request_auth
    return mock_speech


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


def test_captioner_not_ready_before_prepare() -> None:
    captioner = LiveCaptioner()
    assert not captioner.is_ready
    assert not captioner.has_active_worker


def test_prepare_sets_ready() -> None:
    captioner = LiveCaptioner()
    with patch.dict("sys.modules", {"Speech": _mock_speech_modules()}):
        captioner.prepare()
    assert captioner.is_ready


def test_prepare_raises_on_denied() -> None:
    mock_speech = MagicMock()
    mock_speech.SFSpeechRecognizerAuthorizationStatusAuthorized = 0
    mock_speech.SFSpeechRecognizerAuthorizationStatusDenied = 1

    def fake_deny(handler):
        handler(1)  # denied

    mock_speech.SFSpeechRecognizer.requestAuthorization_ = fake_deny

    captioner = LiveCaptioner()
    with (
        patch.dict("sys.modules", {"Speech": mock_speech}),
        pytest.raises(RuntimeError, match="not authorized"),
    ):
        captioner.prepare()


def test_begin_session_before_prepare_raises() -> None:
    captioner = LiveCaptioner()
    with pytest.raises(RuntimeError, match="not prepared"):
        captioner.begin_session()


def test_shutdown_without_session_is_safe() -> None:
    captioner = LiveCaptioner()
    captioner._ready = True
    captioner.shutdown(timeout=1)
    assert not captioner.is_ready


def test_shutdown_is_idempotent() -> None:
    captioner = LiveCaptioner()
    captioner._ready = True
    captioner.shutdown(timeout=1)
    captioner.shutdown(timeout=1)
    assert not captioner.is_ready


def test_end_session_without_begin_is_safe() -> None:
    captioner = LiveCaptioner()
    captioner._ready = True
    captioner.end_session()  # must not raise


# ---------------------------------------------------------------------------
# Bindings
# ---------------------------------------------------------------------------


def test_bind_replaces_callbacks() -> None:
    captioner = LiveCaptioner()
    cb1 = CaptionerBindings(on_realtime_update=lambda t: None)
    cb2 = CaptionerBindings(on_realtime_stabilized=lambda t: None)
    captioner.bind(cb1)
    assert captioner._bindings is cb1
    captioner.bind(cb2)
    assert captioner._bindings is cb2


# ---------------------------------------------------------------------------
# Error emission
# ---------------------------------------------------------------------------


def test_emit_error_calls_binding() -> None:
    errors: list[tuple[str, str]] = []
    captioner = LiveCaptioner(
        CaptionerBindings(on_error=lambda s, m: errors.append((s, m)))
    )
    captioner._emit_error("test", "something broke")
    assert errors == [("test", "something broke")]


def test_emit_error_without_binding_does_not_crash() -> None:
    captioner = LiveCaptioner()
    captioner._emit_error("test", "no handler")  # must not raise


# ---------------------------------------------------------------------------
# Session restart on natural isFinal
# ---------------------------------------------------------------------------


def _captioner_with_mock_recognizer() -> tuple[LiveCaptioner, list]:
    """Return a ready captioner with a mock recognizer that captures result handlers."""
    result_handlers: list = []
    mock_recognizer = MagicMock()
    mock_recognizer.recognitionTaskWithRequest_resultHandler_.side_effect = (
        lambda req, handler: result_handlers.append(handler) or MagicMock()
    )
    captioner = LiveCaptioner()
    captioner._active = True
    captioner._ready = True
    captioner._recognizer = mock_recognizer
    return captioner, result_handlers


def _make_final_result(text: str) -> MagicMock:
    result = MagicMock()
    result.bestTranscription.return_value.formattedString.return_value = text
    result.isFinal.return_value = True
    return result


def test_natural_isfinal_sets_needs_restart() -> None:
    """Regression: natural isFinal must schedule a restart via _needs_restart,
    not call _start_recognition_session() inline from within the result handler.

    Inline restart caused reentrancy in Apple's Speech framework, producing
    a hang and then stale partial words before the new session settled.
    """
    stabilized: list[str] = []
    captioner, result_handlers = _captioner_with_mock_recognizer()
    captioner._bindings = CaptionerBindings(
        on_realtime_stabilized=lambda t: stabilized.append(t)
    )

    mock_speech = MagicMock()
    with patch.dict("sys.modules", {"Speech": mock_speech}):
        captioner._start_recognition_session()
        assert len(result_handlers) == 1

        result_handlers[0](_make_final_result("hello world"), None)

    assert stabilized == ["hello world"]
    # No new session yet — restart is deferred to tick()
    assert len(result_handlers) == 1, (
        "restart must not happen inline inside result handler"
    )
    assert captioner._needs_restart, (
        "_needs_restart flag must be set for tick() to act on"
    )


def test_natural_isfinal_restarts_session() -> None:
    """tick() must start a new recognition session when _needs_restart is set."""
    captioner, result_handlers = _captioner_with_mock_recognizer()

    mock_speech = MagicMock()
    mock_foundation = MagicMock()
    with (
        patch.dict(
            "sys.modules", {"Speech": mock_speech, "Foundation": mock_foundation}
        ),
        patch.object(captioner, "_pump_runloop"),
    ):
        captioner._start_recognition_session()
        captioner._needs_restart = True
        captioner._tick_body()

    assert not captioner._needs_restart
    assert len(result_handlers) == 2, (
        "tick() must start a new session when _needs_restart is True"
    )


def _make_partial_result(text: str) -> MagicMock:
    result = MagicMock()
    result.bestTranscription.return_value.formattedString.return_value = text
    result.isFinal.return_value = False
    return result


# ---------------------------------------------------------------------------
# Incremental commit (scroll-rather-than-fill-and-clear)
# ---------------------------------------------------------------------------


def test_partial_below_threshold_emits_update_only() -> None:
    """Short partial (< threshold) emits on_realtime_update only, not stabilized."""
    updates: list[str] = []
    stabilized: list[str] = []
    captioner, result_handlers = _captioner_with_mock_recognizer()
    captioner._bindings = CaptionerBindings(
        on_realtime_update=lambda t: updates.append(t),
        on_realtime_stabilized=lambda t: stabilized.append(t),
    )

    mock_speech = MagicMock()
    with patch.dict("sys.modules", {"Speech": mock_speech}):
        captioner._start_recognition_session()
        result_handlers[0](_make_partial_result("hello world how are"), None)

    assert updates == ["hello world how are"]
    assert stabilized == []


def test_partial_above_threshold_flushes_chunk_to_stable() -> None:
    """When uncommitted words exceed the commit threshold, a chunk must be sent
    to on_realtime_stabilized and the partial trimmed to the tail words."""
    from scarecrow.live_captioner import _COMMIT_THRESHOLD, _PARTIAL_TAIL

    updates: list[str] = []
    stabilized: list[str] = []
    captioner, result_handlers = _captioner_with_mock_recognizer()
    captioner._bindings = CaptionerBindings(
        on_realtime_update=lambda t: updates.append(t),
        on_realtime_stabilized=lambda t: stabilized.append(t),
    )

    # Build a partial long enough to trigger a flush
    n = _COMMIT_THRESHOLD + _PARTIAL_TAIL + 1
    many_words = " ".join(f"w{i}" for i in range(n))
    mock_speech = MagicMock()
    with patch.dict("sys.modules", {"Speech": mock_speech}):
        captioner._start_recognition_session()
        result_handlers[0](_make_partial_result(many_words), None)

    assert len(stabilized) == 1, "one chunk must have been committed to stable"
    assert len(updates) == 1, "remaining tail must be emitted as partial"
    tail = updates[0].split()
    assert len(tail) == _PARTIAL_TAIL, (
        "partial must contain exactly _PARTIAL_TAIL words"
    )
    # Committed chunk + tail should reconstruct the original text
    assert (stabilized[0] + " " + updates[0]) == many_words


def test_isfinal_commits_remaining_uncommitted_words() -> None:
    """isFinal must commit exactly the words not yet sent via early-commit."""
    from scarecrow.live_captioner import _COMMIT_THRESHOLD, _PARTIAL_TAIL

    stabilized: list[str] = []
    captioner, result_handlers = _captioner_with_mock_recognizer()
    captioner._bindings = CaptionerBindings(
        on_realtime_stabilized=lambda t: stabilized.append(t),
    )

    # Simulate: a large partial that triggers one early commit, then isFinal
    n = _COMMIT_THRESHOLD + _PARTIAL_TAIL + 1
    many_words = " ".join(f"w{i}" for i in range(n))
    more_words = many_words + " extra1 extra2"

    mock_speech = MagicMock()
    with patch.dict("sys.modules", {"Speech": mock_speech}):
        captioner._start_recognition_session()
        result_handlers[0](
            _make_partial_result(many_words), None
        )  # triggers early commit
        result_handlers[0](_make_final_result(more_words), None)  # isFinal

    assert len(stabilized) == 2
    full_committed = stabilized[0] + " " + stabilized[1]
    assert full_committed == more_words, (
        "all words must be accounted for across both commits"
    )


def test_rotation_isfinal_does_not_set_needs_restart() -> None:
    """When rotation already replaced _request, isFinal from the old task must
    not set _needs_restart — rotation already started a new session."""
    captioner, result_handlers = _captioner_with_mock_recognizer()

    mock_speech = MagicMock()
    with patch.dict("sys.modules", {"Speech": mock_speech}):
        captioner._start_recognition_session()
        assert len(result_handlers) == 1

        # Simulate what _rotate_session does: replace _request before isFinal fires
        captioner._request = None
        captioner._task = None

        result_handlers[0](_make_final_result("rotated text"), None)

    assert not captioner._needs_restart, "rotation isFinal must not set _needs_restart"
    assert len(result_handlers) == 1
