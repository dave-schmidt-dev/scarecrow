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
from textual.worker import Worker, WorkerState

from scarecrow.recorder import AudioRecorder
from scarecrow.session import Session

if TYPE_CHECKING:
    from scarecrow.transcriber import Transcriber

log = logging.getLogger(__name__)


class AppState(Enum):
    """Recording state machine states."""

    IDLE = auto()
    RECORDING = auto()
    PAUSED = auto()


_STATE_LABELS: dict[AppState, str] = {
    AppState.IDLE: "Idle",
    AppState.RECORDING: "Recording",
    AppState.PAUSED: "Paused",
}


class StatusBar(Static):
    """A status indicator widget showing the current app state."""

    state: reactive[AppState] = reactive(AppState.IDLE)

    def render(self) -> str:
        label = _STATE_LABELS[self.state]
        return f"[{label}]"

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
        self._transcription_worker: Worker[None] | None = None
        self._suppress_live: bool = False

    def compose(self) -> ComposeResult:
        yield Header()
        yield StatusBar(id="status-bar")
        yield Static("Transcript", classes="pane-label")
        yield RichLog(id="captions", highlight=True, markup=True, wrap=True)
        yield Static("Live", classes="pane-label")
        yield RichLog(
            id="live-log", highlight=False, markup=False, wrap=True, auto_scroll=True
        )
        yield Footer()

    def on_mount(self) -> None:
        self._timer = self.set_interval(1, self._tick, pause=True)
        self._sync_status()
        # Auto-start recording immediately
        self.set_timer(0.1, self._auto_start)

    def _auto_start(self) -> None:
        """Start recording automatically on launch."""
        if not self._preflight_check():
            return
        self._start_recording()

    def _preflight_check(self) -> bool:
        """Verify audio input exists and transcriber is ready. Returns True if OK."""
        import sounddevice as sd

        try:
            devices = sd.query_devices()
        except Exception:
            log.exception("Failed to query audio devices")
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
        """Write an error message to the captions RichLog."""
        self.query_one("#captions", RichLog).write(
            f"[bold red]Error:[/bold red] {message}"
        )

    # ------------------------------------------------------------------
    # Timer
    # ------------------------------------------------------------------

    def _tick(self) -> None:
        self._elapsed += 1
        self._update_footer_time()

    def _update_footer_time(self) -> None:
        h = self._elapsed // 3600
        m = (self._elapsed % 3600) // 60
        s = self._elapsed % 60
        self.sub_title = f"{h:02d}:{m:02d}:{s:02d}"

    # ------------------------------------------------------------------
    # State helpers
    # ------------------------------------------------------------------

    def _sync_status(self) -> None:
        self.query_one(StatusBar).state = self.state

    def watch_state(self, new_state: AppState) -> None:
        self._sync_status()

    # ------------------------------------------------------------------
    # Transcription worker (runs in Thread A)
    # ------------------------------------------------------------------

    def _transcription_loop(self) -> None:
        """Blocking loop that runs in a worker thread."""
        assert self._transcriber is not None
        while self.state in (AppState.RECORDING, AppState.PAUSED):
            try:
                text = self._transcriber.text()
            except Exception as exc:
                log.exception("Transcription error")
                self._safe_call_from_thread(
                    self._show_error, f"Transcription failed: {exc}"
                )
                break
            if text and text.strip():
                self._safe_call_from_thread(self._handle_final_text, text)

    def _handle_final_text(self, text: str) -> None:
        """Called on the main thread when a sentence is finalized."""
        self.append_caption(text)
        if self._session is not None:
            self._session.append_sentence(text)

    # ------------------------------------------------------------------
    # RealtimeSTT callbacks (fire on RealtimeSTT's internal thread)
    # ------------------------------------------------------------------

    def _safe_call_from_thread(self, callback, *args) -> None:
        """call_from_thread that silently ignores 'App is not running'."""
        with contextlib.suppress(RuntimeError):
            self.call_from_thread(callback, *args)

    def _on_realtime_update(self, text: str) -> None:
        if not self._suppress_live:
            self._safe_call_from_thread(self._stream_live_text, text)

    def _on_realtime_stabilized(self, text: str) -> None:
        if not self._suppress_live:
            self._safe_call_from_thread(self._stream_live_text, text)

    def _stream_live_text(self, text: str) -> None:
        """Replace the live log content with the current live text."""
        live_log = self.query_one("#live-log", RichLog)
        live_log.clear()
        live_log.write(text)

    # ------------------------------------------------------------------
    # Actions (bound to keys via BINDINGS)
    # ------------------------------------------------------------------

    def _start_recording(self) -> None:
        """Start recording — called on launch or manually."""
        if self.state is not AppState.IDLE:
            return
        if self._transcriber is None or not self._transcriber.is_ready:
            self._show_error("Transcriber not ready.")
            return

        # Create session and audio recorder
        self._session = Session()
        self._audio_recorder = AudioRecorder(
            output_path=self._session.audio_path,
        )

        # Wire transcriber callbacks to this app instance
        self._transcriber.set_callbacks(
            on_realtime_update=self._on_realtime_update,
            on_realtime_stabilized=self._on_realtime_stabilized,
        )

        # Start audio recording
        try:
            self._audio_recorder.start()
        except Exception as exc:
            log.exception("Failed to start audio recorder")
            self._show_error(f"Could not start audio recorder: {exc}")
            self._audio_recorder = None
            self._session = None
            return

        # Launch transcription worker thread
        self._transcription_worker = self.run_worker(
            self._transcription_loop,
            thread=True,
            name="transcription",
        )

        # Update state and timer
        self._elapsed = 0
        self._suppress_live = False
        self.state = AppState.RECORDING
        self._timer.resume()
        self._update_footer_time()

    def action_pause(self) -> None:
        """Toggle pause/resume — only valid when recording or paused."""
        if self.state is AppState.RECORDING:
            self.state = AppState.PAUSED
            self._timer.pause()
            self._suppress_live = True
            if self._audio_recorder is not None:
                self._audio_recorder.pause()
            self._stream_live_text("")
        elif self.state is AppState.PAUSED:
            self.state = AppState.RECORDING
            self._timer.resume()
            self._suppress_live = False
            if self._audio_recorder is not None:
                self._audio_recorder.resume()

    def action_quit(self) -> None:
        """Stop recording (if active) then quit."""
        self._stop_recording()
        self.exit()

    def _stop_recording(self) -> None:
        """Stop recording components and finalize session."""
        if self.state not in (AppState.RECORDING, AppState.PAUSED):
            return

        self._timer.pause()
        self.state = AppState.IDLE

        # Cancel worker if still running
        if self._transcription_worker is not None:
            with contextlib.suppress(Exception):
                if self._transcription_worker.state is WorkerState.RUNNING:
                    self._transcription_worker.cancel()
            self._transcription_worker = None

        # Stop audio recording
        if self._audio_recorder is not None:
            with contextlib.suppress(Exception):
                self._audio_recorder.stop()
            self._audio_recorder = None

        # Finalize session (close transcript file)
        if self._session is not None:
            with contextlib.suppress(Exception):
                self._session.finalize()
            self._session = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def update_live_preview(self, text: str) -> None:
        """Update the live (partial) caption preview."""
        self._stream_live_text(text)

    def append_caption(self, text: str) -> None:
        """Append settled caption text to the log and clear the live preview."""
        self.query_one("#captions", RichLog).write(text)
        live_log = self.query_one("#live-log", RichLog)
        live_log.clear()
