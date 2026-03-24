"""RealtimeSTT wrapper — dual-model streaming transcription."""

from __future__ import annotations

import multiprocessing
from collections.abc import Callable
from pathlib import Path

import torch.hub

# RealtimeSTT uses torch.multiprocessing internally. Force "spawn" start
# method to avoid inheriting file descriptors on macOS.
if multiprocessing.get_start_method(allow_none=True) != "spawn":
    multiprocessing.set_start_method("spawn", force=True)

from RealtimeSTT import AudioToTextRecorder

from scarecrow import config


def _trust_silero_vad() -> None:
    """Add silero-vad to torch hub trusted repos to avoid interactive prompt."""
    hub_dir = Path(torch.hub.get_dir())
    hub_dir.mkdir(parents=True, exist_ok=True)
    trusted_list = hub_dir / "trusted_list"
    entry = "snakers4/silero-vad"
    if trusted_list.exists() and entry in trusted_list.read_text():
        return
    with trusted_list.open("a") as f:
        f.write(entry + "\n")


_trust_silero_vad()


class Transcriber:
    """Wraps RealtimeSTT's AudioToTextRecorder for dual-model streaming.

    Because AudioToTextRecorder creates multiprocessing.Value objects in
    __init__, it must be constructed BEFORE Textual takes over the terminal
    (Textual modifies file descriptors, breaking mp semaphore creation).

    Use prepare() before app.run(), then wire callbacks with set_callbacks().
    """

    def __init__(
        self,
        on_realtime_update: Callable[[str], None] | None = None,
        on_realtime_stabilized: Callable[[str], None] | None = None,
        on_final_text: Callable[[str], None] | None = None,
    ) -> None:
        self._on_realtime_update_cb = on_realtime_update
        self._on_realtime_stabilized_cb = on_realtime_stabilized
        self._on_final_text_cb = on_final_text
        self.recorder: AudioToTextRecorder | None = None

    def set_callbacks(
        self,
        on_realtime_update: Callable[[str], None] | None = None,
        on_realtime_stabilized: Callable[[str], None] | None = None,
        on_final_text: Callable[[str], None] | None = None,
    ) -> None:
        """Wire up callbacks after construction (e.g. once the App exists)."""
        self._on_realtime_update_cb = on_realtime_update
        self._on_realtime_stabilized_cb = on_realtime_stabilized
        self._on_final_text_cb = on_final_text

    def prepare(self) -> None:
        """Create the AudioToTextRecorder (loads models, creates mp objects).

        Must be called BEFORE Textual's app.run() to avoid fds_to_keep errors.
        """
        self.recorder = AudioToTextRecorder(
            model=config.FINAL_MODEL,
            language=config.LANGUAGE,
            enable_realtime_transcription=True,
            realtime_model_type=config.REALTIME_MODEL,
            realtime_processing_pause=config.REALTIME_PROCESSING_PAUSE,
            use_main_model_for_realtime=False,
            on_realtime_transcription_update=self._on_realtime_update,
            on_realtime_transcription_stabilized=self._on_realtime_stabilized,
            spinner=False,
            beam_size=config.BEAM_SIZE,
            beam_size_realtime=config.BEAM_SIZE_REALTIME,
        )

    def text(self) -> str:
        """Block until the next finalized utterance is ready and return it."""
        result: str = self.recorder.text()  # type: ignore[union-attr]
        if self._on_final_text_cb is not None:
            self._on_final_text_cb(result)
        return result

    def stop(self) -> None:
        """Stop recording."""
        if self.recorder is not None:
            self.recorder.stop()

    def shutdown(self) -> None:
        """Full cleanup — stop recording and release all resources."""
        if self.recorder is not None:
            self.recorder.shutdown()
            self.recorder = None

    @property
    def is_ready(self) -> bool:
        """True if prepare() has been called successfully."""
        return self.recorder is not None

    def _on_realtime_update(self, text: str) -> None:
        if self._on_realtime_update_cb is not None:
            self._on_realtime_update_cb(text)

    def _on_realtime_stabilized(self, text: str) -> None:
        if self._on_realtime_stabilized_cb is not None:
            self._on_realtime_stabilized_cb(text)
