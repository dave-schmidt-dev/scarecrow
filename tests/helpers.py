"""Shared test helpers — importable by any test module."""

from __future__ import annotations

from unittest.mock import MagicMock


def _mock_sys_capture() -> MagicMock:
    """Return a mock SystemAudioCapture that doesn't touch hardware."""
    mock = MagicMock()
    mock.is_recording = True
    mock.is_paused = False
    mock.peak_level = 0.0
    mock.buffer_seconds = 0.0
    mock.start.return_value = None
    mock.stop.return_value = None
    mock.pause.return_value = None
    mock.resume.return_value = None
    mock.drain_to_silence.return_value = None
    mock.drain_buffer.return_value = None
    return mock


def _mock_transcriber() -> MagicMock:
    """Return a mock batch-only Transcriber."""
    mock = MagicMock()
    mock.is_ready = True
    mock.consecutive_failures = 0
    mock.shutdown.return_value = None

    def _shutdown(timeout=5):
        mock.is_ready = False
        return None

    mock.shutdown.side_effect = _shutdown
    return mock


def _mock_recorder() -> MagicMock:
    """Return a mock AudioRecorder that doesn't touch hardware."""
    mock = MagicMock()
    mock.is_recording = True
    mock.is_paused = False
    mock.peak_level = 0.0
    mock.seconds_since_last_callback = 0.0
    mock.buffer_seconds = 0.0
    mock._last_warning = None
    mock._disk_write_failed = False
    mock.default_device_changed = False
    mock.start.return_value = None
    mock.stop.return_value = MagicMock()
    mock.drain_to_silence.return_value = None
    mock.drain_buffer.return_value = None
    return mock


def _app(with_transcriber: bool = False):
    """Return a ScarecrowApp, optionally with a mock transcriber."""
    from scarecrow.app import ScarecrowApp

    if with_transcriber:
        app = ScarecrowApp(transcriber=_mock_transcriber())
        app._preflight_check = lambda: True  # type: ignore[method-assign]
        return app
    return ScarecrowApp()


def _sys_app(**kwargs):
    """Return a ScarecrowApp with sys_audio=True and a ready transcriber."""
    from scarecrow.app import ScarecrowApp

    app = ScarecrowApp(transcriber=_mock_transcriber(), sys_audio=True, **kwargs)
    app._preflight_check = lambda: True  # type: ignore[method-assign]
    return app


def _read_jsonl(path) -> list[dict]:
    """Read a JSONL file and return a list of dicts."""
    import json

    lines = path.read_text(encoding="utf-8").strip().splitlines()
    return [json.loads(line) for line in lines]
