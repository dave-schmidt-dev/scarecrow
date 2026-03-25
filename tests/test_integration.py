"""High-value integration tests that exercise the real transcription pipeline."""

from __future__ import annotations

import signal
import time
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from scarecrow.runtime import model_cache_path
from scarecrow.transcriber import Transcriber, TranscriberBindings

_FIXTURE_PATH = Path("recordings/2026-03-23_22-12-02/audio.wav")


def _models_cached() -> bool:
    from scarecrow import config

    return (
        model_cache_path(config.REALTIME_MODEL) is not None
        and model_cache_path(config.FINAL_MODEL) is not None
    )


def _resample(audio: np.ndarray, src_sr: int, dst_sr: int = 16000) -> np.ndarray:
    if src_sr == dst_sr:
        return audio.astype(np.float32)
    duration = len(audio) / src_sr
    old_times = np.linspace(0, duration, num=len(audio), endpoint=False)
    new_length = int(duration * dst_sr)
    new_times = np.linspace(0, duration, num=new_length, endpoint=False)
    return np.interp(new_times, old_times, audio).astype(np.float32)


@pytest.mark.skipif(
    not _models_cached(),
    reason="models must be cached to run integration test",
)
def test_transcriber_pipeline_with_real_audio_fixture() -> None:
    """Feed audio through live and batch paths with actual models.

    Uses the local fixture when available for content assertions.  Falls back
    to synthetic audio so the pipeline mechanics are still exercised on
    machines without the fixture.
    """
    from scarecrow import config

    stable: list[str] = []
    batches: list[str] = []
    errors: list[tuple[str, str]] = []

    transcriber = Transcriber(
        TranscriberBindings(
            on_realtime_stabilized=stable.append,
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

        check_content = _FIXTURE_PATH.exists()
        if check_content:
            audio, sample_rate = sf.read(_FIXTURE_PATH, dtype="float32")
            audio = _resample(audio, sample_rate)
        else:
            # Synthetic: 2 seconds of low-amplitude noise — exercises the
            # pipeline without requiring the fixture.
            audio = (np.random.randn(32000) * 0.01).astype(np.float32)

        transcriber.begin_session()
        for start in range(0, len(audio), 4096):
            chunk = audio[start : start + 4096]
            transcriber.accept_audio((chunk * 32768).astype("int16").reshape(-1, 1))
            time.sleep(0.05)
        transcriber.end_session()
        transcriber.shutdown(timeout=None)
        transcriber.transcribe_batch(audio, batch_elapsed=30)
    finally:
        signal.alarm(0)

    assert errors == []
    assert config.CONDITION_ON_PREVIOUS_TEXT is False

    if check_content:
        assert stable, "expected at least one stabilized realtime result"
        assert batches, "expected a batch transcription result"
        stable_text = " ".join(stable).lower()
        batch_text = " ".join(batches).lower()
        assert "secretary of state" in stable_text
        assert "secretary of state" in batch_text
