"""Runtime bootstrap and model loading helpers."""

from __future__ import annotations

import logging
import os
import threading
import warnings

from scarecrow import config
from scarecrow.config import Config

log = logging.getLogger(__name__)


def configure_runtime_environment() -> None:
    """Set environment flags needed for offline local model usage."""
    os.environ.setdefault("HF_HUB_DISABLE_IMPLICIT_TOKEN", "1")
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    warnings.filterwarnings("ignore", message=".*unauthenticated requests.*")


class ModelManager:
    """Owns Parakeet model bootstrap."""

    def __init__(self, cfg: Config | None = None) -> None:
        self._parakeet_model = None
        self._lock = threading.Lock()
        self._cfg = cfg or config.config

    def prepare(self) -> None:
        """Initialize runtime state."""
        with self._lock:
            configure_runtime_environment()

    def get_parakeet_model(self):
        """Return the Parakeet model, loading on first use."""
        with self._lock:
            if self._parakeet_model is None:
                from parakeet_mlx import from_pretrained

                self._parakeet_model = from_pretrained(self._cfg.PARAKEET_MODEL)
            return self._parakeet_model

    def release_models(self) -> None:
        """Drop model references so process shutdown can reclaim memory."""
        with self._lock:
            self._parakeet_model = None
