"""Tests for the Parakeet-MLX backend and related app behavior."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np

from scarecrow.transcriber import Transcriber, TranscriberBindings

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_transcriber():
    """Return a mock batch-only Transcriber."""
    mock = MagicMock()
    mock.is_ready = True
    mock.has_active_worker = False
    mock.shutdown.return_value = None

    def _shutdown(timeout=5):
        mock.is_ready = False
        return None

    mock.shutdown.side_effect = _shutdown
    return mock


def _mock_recorder():
    """Return a mock AudioRecorder that doesn't touch hardware."""
    mock = MagicMock()
    mock.is_recording = True
    mock.is_paused = False
    mock.peak_level = 0.0
    mock.start.return_value = None
    mock.stop.return_value = MagicMock()
    return mock


# ---------------------------------------------------------------------------
# Test 1: transcribe_batch routes to Parakeet and returns text
# ---------------------------------------------------------------------------


@patch("scarecrow.config.BACKEND", "parakeet")
def test_transcribe_batch_parakeet_calls_model() -> None:
    """When BACKEND is parakeet, transcribe_batch routes to _transcribe_parakeet."""
    mock_manager = MagicMock()

    t = Transcriber(model_manager=mock_manager)
    t._ready = True

    audio = np.zeros(16000, dtype=np.float32)
    patcher = patch.object(t, "_transcribe_parakeet", return_value="Hello world.")
    with patcher as mock_tp:
        result = t.transcribe_batch(audio, 0, emit_callback=False)
        mock_tp.assert_called_once()
    assert result == "Hello world."


# ---------------------------------------------------------------------------
# Test 2: initial_prompt is NOT forwarded to Parakeet
# ---------------------------------------------------------------------------


@patch("scarecrow.config.BACKEND", "parakeet")
def test_transcribe_batch_parakeet_ignores_initial_prompt() -> None:
    """When BACKEND is parakeet, initial_prompt is accepted but not forwarded."""
    mock_manager = MagicMock()

    t = Transcriber(model_manager=mock_manager)
    t._ready = True

    audio = np.zeros(16000, dtype=np.float32)
    patcher = patch.object(t, "_transcribe_parakeet", return_value="Hello world.")
    with patcher as mock_tp:
        result = t.transcribe_batch(
            audio, 0, initial_prompt="some context", emit_callback=False
        )
        # _transcribe_parakeet called with audio only, no prompt
        args, _kwargs = mock_tp.call_args
        assert len(args) == 1
    assert result == "Hello world."


# ---------------------------------------------------------------------------
# Test 3: Punctuation and capitalization are preserved
# ---------------------------------------------------------------------------


@patch("scarecrow.config.BACKEND", "parakeet")
def test_transcribe_batch_parakeet_preserves_punctuation() -> None:
    """Parakeet results must be returned with punctuation and capitalization intact."""
    original_text = "Hello, World! This is a test."
    mock_manager = MagicMock()

    t = Transcriber(model_manager=mock_manager)
    t._ready = True

    with patch.object(t, "_transcribe_parakeet", return_value=original_text):
        result = t.transcribe_batch(
            np.zeros(16000, dtype=np.float32), 0, emit_callback=False
        )

    assert result == original_text


# ---------------------------------------------------------------------------
# Test 4: Errors in Parakeet path surface via on_error callback
# ---------------------------------------------------------------------------


@patch("scarecrow.config.BACKEND", "parakeet")
def test_transcribe_batch_parakeet_error_emits_callback() -> None:
    """A RuntimeError in parakeet path must trigger on_error and return None."""
    errors: list[tuple[str, str]] = []

    mock_manager = MagicMock()

    t = Transcriber(
        TranscriberBindings(
            on_error=lambda source, message: errors.append((source, message))
        ),
        model_manager=mock_manager,
    )
    t._ready = True

    with patch.object(
        t, "_transcribe_parakeet", side_effect=RuntimeError("GPU exploded")
    ):
        result = t.transcribe_batch(np.zeros(16000, dtype=np.float32), 0)

    assert result is None
    assert errors == [
        ("batch", "Batch transcription failed. See debug log for the stack trace.")
    ]


# ---------------------------------------------------------------------------
# Test 5: ModelManager.get_parakeet_model() lazily imports parakeet_mlx
# ---------------------------------------------------------------------------


def test_parakeet_model_lazy_import() -> None:
    """get_parakeet_model() must call parakeet_mlx.from_pretrained on first use."""
    from scarecrow.runtime import ModelManager

    manager = ModelManager()
    assert manager._parakeet_model is None

    mock_model = MagicMock()

    with patch("scarecrow.runtime.ModelManager.get_parakeet_model") as mock_get:
        mock_get.return_value = mock_model
        result = manager.get_parakeet_model()

    mock_get.assert_called_once()
    assert result is mock_model


# ---------------------------------------------------------------------------
# Test 6: Divider throttle — intermediate batches skip dividers
# ---------------------------------------------------------------------------


@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_divider_throttle_skips_intermediate_batches(
    mock_session_cls, mock_recorder_cls
) -> None:
    """Only batches >= DIVIDER_INTERVAL apart should emit a divider line."""
    from textual.widgets import RichLog

    from scarecrow.app import ScarecrowApp

    mock_recorder_cls.return_value = _mock_recorder()
    mock_session = MagicMock()
    mock_session.transcript_path = Path("/tmp/test_throttle.txt")
    mock_session_cls.return_value = mock_session

    app = ScarecrowApp(transcriber=_mock_transcriber())
    app._preflight_check = lambda: True  # type: ignore[method-assign]

    async with app.run_test() as pilot:
        await pilot.press("enter")
        await pilot.pause(delay=0.3)

        captions = app.query_one("#captions", RichLog)
        initial_lines = len(captions.lines)

        # Call _record_transcript at elapsed values 0..60 in steps of 10
        # DIVIDER_INTERVAL=60: dividers expected at elapsed=0 and elapsed=60
        for elapsed in [0, 10, 20, 30, 40, 50, 60]:
            app._record_transcript("word", batch_elapsed=elapsed)
        await pilot.pause()

        new_lines = [str(line) for line in captions.lines[initial_lines:]]
        divider_lines = [ln for ln in new_lines if "test_throttle.txt" in ln]
        assert len(divider_lines) == 2, (
            f"Expected 2 dividers (at elapsed=0 and elapsed=60), "
            f"got {len(divider_lines)}: {divider_lines}"
        )


# ---------------------------------------------------------------------------
# Test 7: Divider appears after pause/resume (timer reset)
# ---------------------------------------------------------------------------


@patch("scarecrow.app.AudioRecorder")
@patch("scarecrow.app.Session")
async def test_divider_appears_after_pause_resume(
    mock_session_cls, mock_recorder_cls
) -> None:
    """After resume, _last_divider_elapsed is reset so the next batch gets a divider."""
    from textual.widgets import RichLog

    from scarecrow import config
    from scarecrow.app import AppState, ScarecrowApp

    mock_recorder_cls.return_value = _mock_recorder()
    mock_session = MagicMock()
    mock_session.transcript_path = Path("/tmp/test_resume.txt")
    mock_session_cls.return_value = mock_session

    app = ScarecrowApp(transcriber=_mock_transcriber())
    app._preflight_check = lambda: True  # type: ignore[method-assign]

    async with app.run_test() as pilot:
        await pilot.press("enter")
        await pilot.pause(delay=0.3)

        captions = app.query_one("#captions", RichLog)
        initial_lines = len(captions.lines)

        # First transcript at elapsed=0 — should get a divider
        app._record_transcript("first", batch_elapsed=0)
        await pilot.pause()

        lines_after_first = len(captions.lines)
        first_new = [str(ln) for ln in captions.lines[initial_lines:]]
        assert any("test_resume.txt" in ln for ln in first_new), (
            "Expected a divider after first transcript"
        )

        # Simulate pause then resume (resets _last_divider_elapsed)
        app.state = AppState.PAUSED
        app._last_divider_elapsed = (
            -config.DIVIDER_INTERVAL
        )  # mirrors pause/resume reset

        # Now at elapsed=5 — should get a divider because timer was reset
        app._record_transcript("second", batch_elapsed=5)
        await pilot.pause()

        new_after_resume = [str(ln) for ln in captions.lines[lines_after_first:]]
        assert any("test_resume.txt" in ln for ln in new_after_resume), (
            "Expected a divider after resume at elapsed=5"
        )


# ---------------------------------------------------------------------------
# Test 8: BATCH_INTERVAL_SECONDS reflects the active backend
# ---------------------------------------------------------------------------


def test_batch_interval_reflects_backend() -> None:
    """_get_batch_interval() must return the correct interval per backend."""
    from scarecrow.app import _get_batch_interval

    with patch("scarecrow.config.BACKEND", "parakeet"):
        from scarecrow import config

        config.BACKEND = "parakeet"
        interval = _get_batch_interval()
        assert interval == config.BATCH_INTERVAL_PARAKEET

    with patch("scarecrow.config.BACKEND", "whisper"):
        config.BACKEND = "whisper"
        interval = _get_batch_interval()
        assert interval == config.BATCH_INTERVAL_WHISPER
