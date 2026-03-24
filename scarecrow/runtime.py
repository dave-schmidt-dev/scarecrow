"""Runtime bootstrap and model loading helpers."""

from __future__ import annotations

import logging
import os
import warnings
from pathlib import Path

from faster_whisper import WhisperModel
from tqdm import tqdm

from scarecrow import config

log = logging.getLogger(__name__)


def configure_runtime_environment() -> None:
    """Set environment flags needed for offline local model usage."""
    os.environ.setdefault("HF_HUB_DISABLE_IMPLICIT_TOKEN", "1")
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    warnings.filterwarnings("ignore", message=".*unauthenticated requests.*")


def model_cache_path(model_name: str) -> Path | None:
    """Return the HuggingFace cache path for a model, or None if not cached."""
    cache_dir = Path.home() / ".cache" / "huggingface" / "hub"
    path = cache_dir / f"models--Systran--faster-whisper-{model_name}"
    return path if path.exists() else None


def warm_tqdm_lock() -> None:
    """Initialize tqdm's multiprocessing lock before Textual changes fds."""
    tqdm.get_lock()


class ModelManager:
    """Owns Whisper model bootstrap for both realtime and batch paths."""

    def __init__(self) -> None:
        self._live_model: WhisperModel | None = None
        self._batch_model: WhisperModel | None = None

    def prepare(self) -> None:
        """Initialize runtime state and load the realtime model."""
        configure_runtime_environment()
        warm_tqdm_lock()
        if self._live_model is None:
            self._live_model = self._create_model(config.REALTIME_MODEL)

    def get_live_model(self) -> WhisperModel:
        """Return the realtime model, loading bootstrap state if needed."""
        if self._live_model is None:
            self.prepare()
        assert self._live_model is not None
        return self._live_model

    def get_batch_model(self) -> WhisperModel:
        """Return the batch model, loading it on first use."""
        if self._batch_model is None:
            configure_runtime_environment()
            warm_tqdm_lock()
            self._batch_model = self._create_model(config.FINAL_MODEL)
        return self._batch_model

    @staticmethod
    def _create_model(model_name: str) -> WhisperModel:
        return WhisperModel(
            model_name,
            device="cpu",
            compute_type="int8",
        )
