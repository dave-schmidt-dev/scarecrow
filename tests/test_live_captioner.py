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
