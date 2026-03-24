"""Textual TUI application — main entry point for the scarecrow UI."""

from __future__ import annotations

import logging
from enum import Enum, auto
from typing import ClassVar

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.reactive import reactive
from textual.widgets import Footer, Header, RichLog, Static
from textual.worker import Worker, WorkerState

from scarecrow.recorder import AudioRecorder
from scarecrow.session import Session
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
        Binding("r", "record", "Record", show=True),
        Binding("p", "pause", "Pause/Resume", show=True),
        Binding("q", "quit", "Quit", show=True),
    ]

    state: reactive[AppState] = reactive(AppState.IDLE)
    _elapsed: reactive[int] = reactive(0)

    def __init__(self) -> None:
        super().__init__()
        self._session: Session | None = None
        self._audio_recorder: AudioRecorder | None = None
        self._transcriber: Transcriber | None = None
        self._transcription_worker: Worker[None] | None = None
        self._suppress_live: bool = False

    def compose(self) -> ComposeResult:
        yield Header()
        yield StatusBar(id="status-bar")
        yield RichLog(id="captions", highlight=True, markup=True, wrap=True)
        yield Static("", id="live-preview")
        yield Footer()

    def on_mount(self) -> None:
        self._timer = self.set_interval(1, self._tick, pause=True)
        self._sync_status()

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
            except Exception:
                log.exception("Transcription error")
                break
            if text and text.strip():
                self.call_from_thread(self._handle_final_text, text)

    def _handle_final_text(self, text: str) -> None:
        """Called on the main thread when a sentence is finalized."""
        self.append_caption(text)
        if self._session is not None:
            self._session.append_sentence(text)

    # ------------------------------------------------------------------
    # RealtimeSTT callbacks (fire on RealtimeSTT's internal thread)
    # ------------------------------------------------------------------

    def _on_realtime_update(self, text: str) -> None:
        if not self._suppress_live:
            self.call_from_thread(self.update_live_preview, text)

    def _on_realtime_stabilized(self, text: str) -> None:
        if not self._suppress_live:
            self.call_from_thread(self.update_live_preview, text)

    # ------------------------------------------------------------------
    # Actions (bound to keys via BINDINGS)
    # ------------------------------------------------------------------

    def action_record(self) -> None:
        """Start recording — only valid from idle state."""
        if self.state is not AppState.IDLE:
            return

        # Create session and components
        self._session = Session()
        self._audio_recorder = AudioRecorder(
            output_path=self._session.audio_path,
        )
        self._transcriber = Transcriber(
            on_realtime_update=self._on_realtime_update,
            on_realtime_stabilized=self._on_realtime_stabilized,
        )

        # Start audio recording
        self._audio_recorder.start()

        # Start transcription
        self._transcriber.start()

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
            self.update_live_preview("")
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
        """Stop all recording components and finalize the session."""
        if self.state not in (AppState.RECORDING, AppState.PAUSED):
            return

        self._timer.pause()
        self.state = AppState.IDLE

        # Stop transcription first (unblocks text() loop)
        if self._transcriber is not None:
            self._transcriber.shutdown()
            self._transcriber = None

        # Cancel worker if still running
        if self._transcription_worker is not None:
            if self._transcription_worker.state is WorkerState.RUNNING:
                self._transcription_worker.cancel()
            self._transcription_worker = None

        # Stop audio recording
        if self._audio_recorder is not None:
            self._audio_recorder.stop()
            self._audio_recorder = None

        # Finalize session (close transcript file)
        if self._session is not None:
            self._session.finalize()
            self._session = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def update_live_preview(self, text: str) -> None:
        """Update the live (partial) caption preview."""
        self.query_one("#live-preview", Static).update(text)

    def append_caption(self, text: str) -> None:
        """Append settled caption text to the log and clear the live preview."""
        self.query_one("#captions", RichLog).write(text)
        self.query_one("#live-preview", Static).update("")
