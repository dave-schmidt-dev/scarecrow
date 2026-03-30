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
    mock.drain_to_silence.return_value = None
    return mock


# ---------------------------------------------------------------------------
# Test 1: transcribe_batch routes to Parakeet and returns text
# ---------------------------------------------------------------------------


def test_transcribe_batch_parakeet_calls_model() -> None:
    """transcribe_batch routes to _transcribe_parakeet."""
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
# Test 2: Punctuation and capitalization are preserved
# ---------------------------------------------------------------------------


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
# Test 3: Errors in Parakeet path surface via on_error callback
# ---------------------------------------------------------------------------


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

    exc = RuntimeError("GPU exploded")
    with (
        patch.object(t, "_transcribe_parakeet", side_effect=exc),
        patch("scarecrow.transcriber.time.sleep"),
    ):
        result = t.transcribe_batch(np.zeros(16000, dtype=np.float32), 0)

    assert result is None
    assert errors == [
        ("batch", "Batch transcription failed after retries. Audio is still recording.")
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
    mock_parakeet = MagicMock()
    mock_parakeet.from_pretrained.return_value = mock_model

    with patch.dict("sys.modules", {"parakeet_mlx": mock_parakeet}):
        result = manager.get_parakeet_model()

    mock_parakeet.from_pretrained.assert_called_once()
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
