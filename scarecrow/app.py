"""Textual TUI application — main entry point for the scarecrow UI."""

from __future__ import annotations

import contextlib
import logging
from enum import Enum, auto
from typing import TYPE_CHECKING, ClassVar

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.reactive import reactive
from textual.widgets import Footer, Header, RichLog, Static

from scarecrow.recorder import AudioRecorder
from scarecrow.session import Session

if TYPE_CHECKING:
    from scarecrow.transcriber import Transcriber

log = logging.getLogger(__name__)

BATCH_INTERVAL_SECONDS = 30


class AppState(Enum):
    IDLE = auto()
    RECORDING = auto()
    PAUSED = auto()


_STATE_LABELS: dict[AppState, str] = {
    AppState.IDLE: "Idle",
    AppState.RECORDING: "Recording",
    AppState.PAUSED: "Paused",
}


class StatusBar(Static):
    state: reactive[AppState] = reactive(AppState.IDLE)

    def render(self) -> str:
        return f"[{_STATE_LABELS[self.state]}]"

    def watch_state(self, new_state: AppState) -> None:
        self.remove_class("state-idle", "state-recording", "state-paused")
        self.add_class(f"state-{new_state.name.lower()}")


class ScarecrowApp(App[None]):
    """Scarecrow — always-recording TUI with live captions."""

    TITLE = "Scarecrow"
    CSS_PATH = "app.tcss"

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("p", "pause", "Pause/Resume", show=True),
        Binding("q", "quit", "Quit", show=True),
    ]

    state: reactive[AppState] = reactive(AppState.IDLE)
    _elapsed: reactive[int] = reactive(0)

    def __init__(self, transcriber: Transcriber | None = None) -> None:
        super().__init__()
        self._session: Session | None = None
        self._audio_recorder: AudioRecorder | None = None
        self._transcriber: Transcriber | None = transcriber
        self._suppress_live: bool = False

    def compose(self) -> ComposeResult:
        from scarecrow import config

        yield Header()
        yield StatusBar(id="status-bar")
        yield Static(
            f"Transcript  [dim]({config.FINAL_MODEL} · "
            f"every {BATCH_INTERVAL_SECONDS}s)[/dim]",
            classes="pane-label",
        )
        yield RichLog(id="captions", highlight=True, markup=True, wrap=True)
        yield Static(
            f"Live  [dim]({config.REALTIME_MODEL})[/dim]",
            classes="pane-label",
        )
        yield RichLog(
            id="live-log",
            highlight=False,
            markup=False,
            wrap=True,
            auto_scroll=True,
        )
        yield Footer()

    def on_mount(self) -> None:
        self._timer = self.set_interval(1, self._tick, pause=True)
        self._sync_status()
        self.set_timer(0.1, self._auto_start)

    def _auto_start(self) -> None:
        if not self._preflight_check():
            return
        self._start_recording()

    def _preflight_check(self) -> bool:
        import sounddevice as sd

        try:
            devices = sd.query_devices()
        except Exception:
            self._show_error("Could not query audio devices.")
            return False

        try:
            has_input = any(
                d.get("max_input_channels", 0) > 0  # type: ignore[union-attr]
                for d in devices
            )
        except TypeError:
            has_input = devices.get("max_input_channels", 0) > 0  # type: ignore[union-attr]
        if not has_input:
            self._show_error("No audio input devices found.")
            return False

        if self._transcriber is None or not self._transcriber.is_ready:
            self._show_error("Transcriber not initialized.")
            return False

        return True

    def _show_error(self, message: str) -> None:
        self.query_one("#captions", RichLog).write(
            f"[bold red]Error:[/bold red] {message}"
        )

    # ------------------------------------------------------------------
    # Timer
    # ------------------------------------------------------------------

    def _tick(self) -> None:
        self._elapsed += 1
        h = self._elapsed // 3600
        m = (self._elapsed % 3600) // 60
        s = self._elapsed % 60
        self.sub_title = f"{h:02d}:{m:02d}:{s:02d}"

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    def _sync_status(self) -> None:
        self.query_one(StatusBar).state = self.state

    def watch_state(self, new_state: AppState) -> None:
        self._sync_status()

    # ------------------------------------------------------------------
    # Live pane — driven by RealtimeSTT callbacks (tiny.en, continuous)
    # ------------------------------------------------------------------

    def _safe_call(self, callback, *args) -> None:
        with contextlib.suppress(RuntimeError):
            self.call_from_thread(callback, *args)

    def _on_realtime_update(self, text: str) -> None:
        if not self._suppress_live and text:
            self._safe_call(self._update_live, text)

    def _on_realtime_stabilized(self, text: str) -> None:
        if not self._suppress_live and text:
            self._safe_call(self._update_live, text)

    def _update_live(self, text: str) -> None:
        live_log = self.query_one("#live-log", RichLog)
        live_log.clear()
        live_log.write(text)

    # ------------------------------------------------------------------
    # Transcript pane — batch transcription with medium.en every 30s
    # ------------------------------------------------------------------

    def _batch_transcribe(self) -> None:
        """Drain audio buffer and transcribe with medium.en in a worker."""
        if self._audio_recorder is None or self._transcriber is None:
            return
        if self.state not in (AppState.RECORDING, AppState.PAUSED):
            return

        audio = self._audio_recorder.drain_buffer()
        if audio is None or len(audio) == 0:
            return

        # Run in a thread so we don't block the UI
        self.run_worker(
            lambda: self._run_batch(audio),
            thread=True,
            name="batch-transcribe",
        )

    def _run_batch(self, audio) -> None:
        """Run medium.en on audio chunk (called in worker thread)."""
        from scarecrow import config

        try:
            model = self._get_batch_model()
            segments, _ = model.transcribe(
                audio,
                language=config.LANGUAGE,
                beam_size=config.BEAM_SIZE,
                vad_filter=True,
            )
            text = " ".join(seg.text.strip() for seg in segments)
            if text.strip():
                self._safe_call(self._append_transcript, text.strip())
        except Exception:
            log.exception("Batch transcription failed")

    def _get_batch_model(self):
        """Lazily load the batch transcription model."""
        if not hasattr(self, "_batch_model"):
            from faster_whisper import WhisperModel

            from scarecrow import config

            self._batch_model = WhisperModel(
                config.FINAL_MODEL,
                device="cpu",
                compute_type="int8",
            )
        return self._batch_model

    def _append_transcript(self, text: str) -> None:
        """Append batch-transcribed text to the transcript pane and file."""
        self.query_one("#captions", RichLog).write(text)
        if self._session is not None:
            self._session.append_sentence(text)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _start_recording(self) -> None:
        if self.state is not AppState.IDLE:
            return
        if self._transcriber is None or not self._transcriber.is_ready:
            self._show_error("Transcriber not ready.")
            return

        self._session = Session()
        self._audio_recorder = AudioRecorder(
            output_path=self._session.audio_path,
        )

        self._transcriber.set_callbacks(
            on_realtime_update=self._on_realtime_update,
            on_realtime_stabilized=self._on_realtime_stabilized,
        )

        try:
            self._audio_recorder.start()
        except Exception as exc:
            log.exception("Failed to start audio recorder")
            self._show_error(f"Could not start audio recorder: {exc}")
            self._audio_recorder = None
            self._session = None
            return

        # Start continuous recording via RealtimeSTT (for live callbacks)
        assert self._transcriber.recorder is not None
        self._transcriber.recorder.start()

        # Schedule batch transcription every 30 seconds
        self._batch_timer = self.set_interval(
            BATCH_INTERVAL_SECONDS, self._batch_transcribe
        )

        self._elapsed = 0
        self._suppress_live = False
        self.state = AppState.RECORDING
        self._timer.resume()
        self._update_live("Listening…")

    def action_pause(self) -> None:
        if self.state is AppState.RECORDING:
            self.state = AppState.PAUSED
            self._timer.pause()
            self._suppress_live = True
            if self._audio_recorder is not None:
                self._audio_recorder.pause()
        elif self.state is AppState.PAUSED:
            self.state = AppState.RECORDING
            self._timer.resume()
            self._suppress_live = False
            if self._audio_recorder is not None:
                self._audio_recorder.resume()

    def action_quit(self) -> None:
        self._stop_recording()
        self.exit()

    def _stop_recording(self) -> None:
        if self.state not in (AppState.RECORDING, AppState.PAUSED):
            return

        self._timer.pause()
        if hasattr(self, "_batch_timer"):
            self._batch_timer.pause()

        # Final batch transcription of remaining audio
        self._batch_transcribe()

        self.state = AppState.IDLE

        if self._audio_recorder is not None:
            with contextlib.suppress(Exception):
                self._audio_recorder.stop()
            self._audio_recorder = None

        if self._session is not None:
            with contextlib.suppress(Exception):
                self._session.finalize()
            self._session = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def update_live_preview(self, text: str) -> None:
        self._update_live(text)

    def append_caption(self, text: str) -> None:
        self.query_one("#captions", RichLog).write(text)
