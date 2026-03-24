"""Textual TUI application — main entry point for the scarecrow UI."""

from __future__ import annotations

from enum import Enum, auto
from typing import ClassVar

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.reactive import reactive
from textual.widgets import Footer, Header, RichLog, Static


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
    # Actions (bound to keys via BINDINGS)
    # ------------------------------------------------------------------

    def action_record(self) -> None:
        """Start recording — only valid from idle state."""
        if self.state is AppState.IDLE:
            self._elapsed = 0
            self.state = AppState.RECORDING
            self._timer.resume()
            self._update_footer_time()

    def action_pause(self) -> None:
        """Toggle pause/resume — only valid when recording or paused."""
        if self.state is AppState.RECORDING:
            self.state = AppState.PAUSED
            self._timer.pause()
        elif self.state is AppState.PAUSED:
            self.state = AppState.RECORDING
            self._timer.resume()

    def action_quit(self) -> None:
        """Stop recording (if active) then quit."""
        if self.state in (AppState.RECORDING, AppState.PAUSED):
            self._timer.pause()
            self.state = AppState.IDLE
        self.exit()

    # ------------------------------------------------------------------
    # Public API — Phase 6 integration points
    # ------------------------------------------------------------------

    def update_live_preview(self, text: str) -> None:
        """Update the live (partial) caption preview."""
        self.query_one("#live-preview", Static).update(text)

    def append_caption(self, text: str) -> None:
        """Append settled caption text to the log and clear the live preview."""
        self.query_one("#captions", RichLog).write(text)
        self.query_one("#live-preview", Static).update("")
