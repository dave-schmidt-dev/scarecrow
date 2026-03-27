"""Runtime bootstrap and model loading helpers."""

from __future__ import annotations

import logging
import os
import threading
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
    """Return the HuggingFace cache path for a model, or None if not cached.

    Handles both short names (e.g. "medium.en" → Systran/faster-whisper-medium.en)
    and full repo IDs (e.g. "deepdml/faster-whisper-large-v3-turbo-ct2").
    """
    cache_dir = Path.home() / ".cache" / "huggingface" / "hub"
    if "/" in model_name:
        # Full repo ID: org/repo → models--org--repo
        path = cache_dir / f"models--{model_name.replace('/', '--')}"
    else:
        path = cache_dir / f"models--Systran--faster-whisper-{model_name}"
    return path if path.exists() else None


def warm_tqdm_lock() -> None:
    """Initialize tqdm's multiprocessing lock before Textual changes fds."""
    tqdm.get_lock()


class ModelManager:
    """Owns Whisper model bootstrap for both realtime and batch paths."""

    def __init__(self) -> None:
        self._batch_model: WhisperModel | None = None
        self._parakeet_model = None
        self._lock = threading.Lock()

    def prepare(self) -> None:
        """Initialize runtime state."""
        with self._lock:
            self._prepare_unlocked()

    def _prepare_unlocked(self) -> None:
        """Internal prepare — caller must hold self._lock."""
        configure_runtime_environment()
        warm_tqdm_lock()

    def get_batch_model(self) -> WhisperModel:
        """Return the batch model, loading it on first use."""
        with self._lock:
            if self._batch_model is None:
                configure_runtime_environment()
                warm_tqdm_lock()
                self._batch_model = self._create_model(config.FINAL_MODEL)
            return self._batch_model

    def get_parakeet_model(self):
        """Return the Parakeet model, loading on first use."""
        with self._lock:
            if self._parakeet_model is None:
                from parakeet_mlx import from_pretrained

                self._parakeet_model = from_pretrained(config.PARAKEET_MODEL)
            return self._parakeet_model

    def release_models(self) -> None:
        """Drop model references so process shutdown can reclaim memory."""
        with self._lock:
            self._batch_model = None
            self._parakeet_model = None

    @staticmethod
    def _create_model(model_name: str) -> WhisperModel:
        # Temporarily allow network access if model isn't cached yet
        need_download = model_cache_path(model_name) is None
        old_offline = os.environ.get("HF_HUB_OFFLINE")
        if need_download:
            os.environ.pop("HF_HUB_OFFLINE", None)
        try:
            return WhisperModel(
                model_name,
                device="cpu",
                compute_type="int8",
            )
        finally:
            if need_download:
                if old_offline is not None:
                    os.environ["HF_HUB_OFFLINE"] = old_offline
                else:
                    os.environ.pop("HF_HUB_OFFLINE", None)
