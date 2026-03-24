"""RealtimeSTT wrapper — dual-model streaming transcription."""

from __future__ import annotations

from collections.abc import Callable

from RealtimeSTT import AudioToTextRecorder

from scarecrow import config


class Transcriber:
    """Wraps RealtimeSTT's AudioToTextRecorder for dual-model streaming."""

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

    def start(self) -> None:
        """Create and start the AudioToTextRecorder (triggers mic access)."""
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
        """Block until the next finalized utterance is ready and return it.

        This is the call the worker thread makes in a loop. AudioToTextRecorder
        accepts an optional callback; passing None means it returns the text
        directly as a string.
        """
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

    def _on_realtime_update(self, text: str) -> None:
        """Internal callback — forwards live partial text to the user callback."""
        if self._on_realtime_update_cb is not None:
            self._on_realtime_update_cb(text)

    def _on_realtime_stabilized(self, text: str) -> None:
        """Internal callback — forwards stabilized partial text to the user callback."""
        if self._on_realtime_stabilized_cb is not None:
            self._on_realtime_stabilized_cb(text)
