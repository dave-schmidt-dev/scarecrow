"""High-value integration tests that exercise the real batch transcription pipeline."""

from __future__ import annotations

import importlib.util
import signal
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from scarecrow.transcriber import Transcriber, TranscriberBindings

_FIXTURE_PATH = Path("recordings/2026-03-23_22-12-02/audio.wav")


def _resample(audio: np.ndarray, src_sr: int, dst_sr: int = 16000) -> np.ndarray:
    if src_sr == dst_sr:
        return audio.astype(np.float32)
    duration = len(audio) / src_sr
    old_times = np.linspace(0, duration, num=len(audio), endpoint=False)
    new_length = int(duration * dst_sr)
    new_times = np.linspace(0, duration, num=new_length, endpoint=False)
    return np.interp(new_times, old_times, audio).astype(np.float32)


@pytest.mark.skipif(
    not importlib.util.find_spec("parakeet_mlx"),
    reason="parakeet_mlx not installed",
)
def test_batch_transcription_with_real_audio_fixture() -> None:
    """Feed audio through the batch path with actual models.

    Uses the local fixture when available for content assertions.  Falls back
    to synthetic audio so the pipeline mechanics are still exercised on
    machines without the fixture.
    """

    batches: list[str] = []
    errors: list[tuple[str, str]] = []

    transcriber = Transcriber(
        TranscriberBindings(
            on_batch_result=lambda text, elapsed: batches.append(text),
            on_error=lambda source, message: errors.append((source, message)),
        )
    )

    def _timeout_handler(signum, frame):
        raise TimeoutError(
            "real transcription pipeline timed out after 30s; "
            "model loading or inference regressed"
        )

    signal.signal(signal.SIGALRM, _timeout_handler)
    signal.alarm(30)
    try:
        transcriber.prepare()
        transcriber.preload_batch_model()

        check_content = _FIXTURE_PATH.exists()
        if check_content:
            audio, sample_rate = sf.read(_FIXTURE_PATH, dtype="float32")
            audio = _resample(audio, sample_rate)
        else:
            audio = (np.random.randn(32000) * 0.01).astype(np.float32)

        transcriber.transcribe_batch(audio, batch_elapsed=30)
        transcriber.shutdown()
    finally:
        signal.alarm(0)

    assert errors == []

    if check_content:
        assert batches, "expected a batch transcription result"
        batch_text = " ".join(batches).lower()
        assert "secretary of state" in batch_text
