"""Textual TUI application for Scarecrow."""

from __future__ import annotations

import logging
from enum import Enum, auto
from typing import TYPE_CHECKING, ClassVar

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.css.query import NoMatches
from textual.reactive import reactive
from textual.widgets import Footer, RichLog, Static

from scarecrow import config
from scarecrow.recorder import AudioRecorder
from scarecrow.session import Session
from scarecrow.transcriber import TranscriberBindings

if TYPE_CHECKING:
    from scarecrow.transcriber import Transcriber

log = logging.getLogger(__name__)

BATCH_INTERVAL_SECONDS = 30


class AppState(Enum):
    IDLE = auto()
    RECORDING = auto()
    PAUSED = auto()


_STATE_STYLE: dict[AppState, tuple[str, str, str]] = {
    AppState.IDLE: ("IDLE", "dim", ""),
    AppState.RECORDING: ("REC", "bold white on dark_red", "\U0001f3a4"),
    AppState.PAUSED: ("PAUSED", "bold black on yellow", ""),
}


class InfoBar(Static):
    """Top bar with state, elapsed time, word count, countdown, and status."""

    state: reactive[AppState] = reactive(AppState.IDLE)
    elapsed: reactive[int] = reactive(0)
    word_count: reactive[int] = reactive(0)
    batch_countdown: reactive[int] = reactive(BATCH_INTERVAL_SECONDS)
    status_message: reactive[str] = reactive("")
    status_is_error: reactive[bool] = reactive(False)

    def render(self) -> Text:
        label, style, icon = _STATE_STYLE[self.state]
        text = Text()
        text.append(f" {label} ", style=style)
        if icon:
            text.append(f" {icon}")
        text.append("  ")

        h = self.elapsed // 3600
        m = (self.elapsed % 3600) // 60
        s = self.elapsed % 60
        text.append(f"{h:02d}:{m:02d}:{s:02d}", style="bold")
        text.append("  ")

        text.append(f"{self.word_count}", style="bold")
        text.append(" words", style="dim")
        text.append("  ")

        if self.state in (AppState.RECORDING, AppState.PAUSED):
            text.append("batch ", style="dim")
            text.append(f"{self.batch_countdown}s", style="bold")

        if self.status_message:
            text.append("  ")
            text.append(
                self.status_message,
                style="bold red" if self.status_is_error else "dim",
            )

        return text


class ScarecrowApp(App[None]):
    """Always-recording TUI with realtime and batch transcription."""

    TITLE = "Scarecrow"
    CSS_PATH = "app.tcss"
    ENABLE_COMMAND_PALETTE = False

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("p", "pause", "Pause/Resume", show=True),
        Binding("q", "quit", "Quit", show=True),
    ]

    state: reactive[AppState] = reactive(AppState.IDLE)
    _elapsed: reactive[int] = reactive(0)
    _word_count: reactive[int] = reactive(0)
    _batch_countdown: reactive[int] = reactive(BATCH_INTERVAL_SECONDS)

    def __init__(self, transcriber: Transcriber | None = None) -> None:
        super().__init__()
        self._session: Session | None = None
        self._audio_recorder: AudioRecorder | None = None
        self._transcriber: Transcriber | None = transcriber
        self._live_stable: list[str] = []
        self._live_partial: str = ""
        self._status_message: str = ""
        self._status_is_error = False
        self._shutdown_summary = ""

    def compose(self) -> ComposeResult:
        yield InfoBar(id="info-bar")
        yield Static(
            (
                f"Transcript  [dim]({config.FINAL_MODEL} · "
                f"every {BATCH_INTERVAL_SECONDS}s)[/dim]"
            ),
            classes="pane-label",
        )
        yield RichLog(
            id="captions",
            highlight=True,
            markup=True,
            wrap=True,
            min_width=0,
        )
        yield Static(
            f"Live  [dim]({config.REALTIME_MODEL})[/dim]",
            classes="pane-label",
        )
        with VerticalScroll(id="live-pane"):
            yield Static(id="live-content")
        yield Footer()

    def on_mount(self) -> None:
        self._tick_timer = self.set_interval(1, self._tick, pause=True)
        self._sync_info_bar()
        self.set_timer(0.1, self._auto_start)

    def _auto_start(self) -> None:
        if self._preflight_check():
            self._start_recording()

    def _preflight_check(self) -> bool:
        import sounddevice as sd

        try:
            devices = sd.query_devices()
        except Exception as exc:
            log.exception("Audio device query failed")
            self._show_error(f"Could not query audio devices: {exc}")
            return False

        try:
            has_input = any(d.get("max_input_channels", 0) > 0 for d in devices)
        except TypeError:
            has_input = devices.get("max_input_channels", 0) > 0

        if not has_input:
            self._show_error("No audio input devices found.")
            return False

        if self._transcriber is None or not self._transcriber.is_ready:
            self._show_error("Transcriber not initialized.")
            return False

        return True

    def _tick(self) -> None:
        self._elapsed += 1
        self._batch_countdown = max(0, self._batch_countdown - 1)
        self._sync_info_bar()

    def _sync_info_bar(self) -> None:
        if not self.is_mounted:
            return
        try:
            bar = self.query_one(InfoBar)
        except NoMatches:
            return
        bar.state = self.state
        bar.elapsed = self._elapsed
        bar.word_count = self._word_count
        bar.batch_countdown = self._batch_countdown
        bar.status_message = self._status_message
        bar.status_is_error = self._status_is_error

    def watch_state(self, _new_state: AppState) -> None:
        self._sync_info_bar()

    def _set_status(self, message: str, *, error: bool = False) -> None:
        self._status_message = message
        self._status_is_error = error
        self._sync_info_bar()

    def _show_error(self, message: str) -> None:
        self._set_status(message, error=True)
        if not self.is_mounted:
            log.error("UI error before mount: %s", message)
            return
        try:
            self.query_one("#captions", RichLog).write(
                f"[bold red]Error:[/bold red] {message}"
            )
        except NoMatches:
            log.error("Error pane unavailable: %s", message)

    def _post_to_ui(self, callback, *args) -> None:
        try:
            self.call_from_thread(callback, *args)
        except RuntimeError:
            if self.state is not AppState.IDLE:
                log.error("UI callback failed while app still active: %s", callback)
            else:
                log.debug("UI callback skipped during shutdown: %s", callback)

    def _bind_transcriber(self) -> None:
        if self._transcriber is None:
            return
        self._transcriber.bind(
            TranscriberBindings(
                on_realtime_update=self._on_realtime_update,
                on_realtime_stabilized=self._on_realtime_stabilized,
                on_batch_result=self._on_batch_result,
                on_error=self._on_transcriber_error,
            )
        )

    def _on_realtime_update(self, text: str) -> None:
        if text:
            self._post_to_ui(self._set_live_partial, text)

    def _on_realtime_stabilized(self, text: str) -> None:
        if text:
            self._post_to_ui(self._append_live, text)

    def _on_batch_result(self, text: str, batch_elapsed: int) -> None:
        self._post_to_ui(self._append_transcript, text, batch_elapsed)

    def _on_transcriber_error(self, source: str, message: str) -> None:
        self._post_to_ui(self._show_error, f"{source}: {message}")

    def _render_live(self) -> None:
        if not self.is_mounted:
            return
        try:
            content = self.query_one("#live-content", Static)
            pane = self.query_one("#live-pane", VerticalScroll)
        except NoMatches:
            return

        text = Text()
        visible_lines = self._live_stable[-config.LIVE_HISTORY_LIMIT :]
        for index, line in enumerate(visible_lines):
            if index:
                text.append("\n")
            text.append(line)
        if self._live_partial:
            if visible_lines:
                text.append("\n")
            text.append(self._live_partial, style="dim")
        content.update(text if text.plain else "Listening…")
        self.call_after_refresh(pane.scroll_end, animate=False)

    def _set_live_partial(self, text: str) -> None:
        self._live_partial = text
        if self.state is AppState.RECORDING:
            self._set_status("Listening…")
        self._render_live()

    def _append_live(self, text: str) -> None:
        self._live_stable.append(text)
        if len(self._live_stable) > config.LIVE_HISTORY_LIMIT:
            self._live_stable = self._live_stable[-config.LIVE_HISTORY_LIMIT :]
        self._live_partial = ""
        if self.state is AppState.RECORDING:
            self._set_status("Listening…")
        self._render_live()

    def _update_live_message(self, text: str) -> None:
        self._live_stable = [text]
        self._live_partial = ""
        self._render_live()

    def _update_live(self, text: str) -> None:
        """Backward-compatible full live pane update helper."""
        self._update_live_message(text)

    def _update_live_partial(self, text: str) -> None:
        """Backward-compatible partial update helper."""
        self._set_live_partial(text)

    def _batch_transcribe(self) -> None:
        if self._audio_recorder is None or self._transcriber is None:
            return

        audio = self._audio_recorder.drain_buffer()
        if audio is None or len(audio) == 0:
            return

        batch_elapsed = self._elapsed
        self.run_worker(
            lambda: self._transcriber.transcribe_batch(audio, batch_elapsed),
            thread=True,
            name="batch-transcribe",
        )

    def _append_transcript(self, text: str, batch_elapsed: int | None = None) -> None:
        captions = self.query_one("#captions", RichLog)
        if self._session is not None:
            path = self._session.transcript_path
            elapsed = batch_elapsed if batch_elapsed is not None else self._elapsed
            h = elapsed // 3600
            m = (elapsed % 3600) // 60
            s = elapsed % 60
            ts = f"{h:02d}:{m:02d}:{s:02d}"
            divider = f"── {ts} · {path} ──"
            captions.write(f"[dim]{divider}[/dim]")
            self._session.append_sentence(f"\n{divider}")
        captions.write(text)
        if self._session is not None:
            self._session.append_sentence(text)
        self._word_count += len(text.split())
        self._set_status("Listening…" if self.state is AppState.RECORDING else "")
        self._sync_info_bar()

    def _write_pause_marker(self) -> None:
        h = self._elapsed // 3600
        m = (self._elapsed % 3600) // 60
        s = self._elapsed % 60
        ts = f"{h:02d}:{m:02d}:{s:02d}"
        marker = f"── {ts} · Recording paused ──"
        self.query_one("#captions", RichLog).write(f"[dim]{marker}[/dim]")
        if self._session is not None:
            self._session.append_sentence(f"\n{marker}")

    def _start_recording(self) -> None:
        if self.state is not AppState.IDLE:
            return
        if self._transcriber is None or not self._transcriber.is_ready:
            self._show_error("Transcriber not ready.")
            return

        self._bind_transcriber()
        self._session = Session(base_dir=config.DEFAULT_RECORDINGS_DIR)
        self._audio_recorder = AudioRecorder(
            output_path=self._session.audio_path,
            sample_rate=config.SAMPLE_RATE,
            on_audio=self._transcriber.accept_audio,
        )

        try:
            self._audio_recorder.start()
            self._transcriber.begin_session()
        except Exception as exc:
            log.exception("Failed to start recording session")
            self._show_error(f"Could not start recording session: {exc}")
            self._audio_recorder = None
            self._session = None
            return

        self._batch_timer = self.set_interval(
            BATCH_INTERVAL_SECONDS,
            self._on_batch_tick,
        )
        self._elapsed = 0
        self._batch_countdown = BATCH_INTERVAL_SECONDS
        self._word_count = 0
        self._live_stable = []
        self._live_partial = ""
        self.state = AppState.RECORDING
        self._tick_timer.resume()
        self._set_status("Listening…")
        self._update_live("Listening…")

    def _on_batch_tick(self) -> None:
        self._batch_countdown = BATCH_INTERVAL_SECONDS
        self._sync_info_bar()
        if self.state is AppState.RECORDING:
            self._batch_transcribe()
        elif self.state is AppState.PAUSED:
            self._write_pause_marker()

    def action_pause(self) -> None:
        if self.state is AppState.RECORDING:
            self._batch_transcribe()
            self.state = AppState.PAUSED
            if self._audio_recorder is not None:
                self._audio_recorder.pause()
            self._set_status("Paused")
            self._set_live_partial("Paused")
            self._write_pause_marker()
            return

        if self.state is AppState.PAUSED:
            self.state = AppState.RECORDING
            if self._audio_recorder is not None:
                self._audio_recorder.resume()
            self._batch_countdown = BATCH_INTERVAL_SECONDS
            self._set_status("Listening…")
            self._live_partial = ""
            self._render_live()
            self._sync_info_bar()

    def action_quit(self) -> None:
        self._shutdown_summary = self._collect_shutdown_metrics()
        self._set_status("Shutting down…")
        self._update_live("Shutting down…")
        self.set_timer(0.3, self._deferred_quit)

    def _collect_shutdown_metrics(self) -> str:
        h = self._elapsed // 3600
        m = (self._elapsed % 3600) // 60
        s = self._elapsed % 60
        duration = f"{h:02d}:{m:02d}:{s:02d}"

        lines = [
            f"  Duration:    {duration}",
            f"  Words:       {self._word_count}",
        ]

        if self._session is not None:
            session_dir = self._session.session_dir
            lines.append(f"  Session:     {session_dir}")

            audio_path = self._session.audio_path
            if audio_path.exists():
                size_mb = audio_path.stat().st_size / (1024 * 1024)
                lines.append(f"  Audio:       {audio_path} ({size_mb:.1f} MB)")

            transcript_path = self._session.transcript_path
            if transcript_path.exists():
                size_kb = transcript_path.stat().st_size / 1024
                lines.append(f"  Transcript:  {transcript_path} ({size_kb:.1f} KB)")

        return "\n".join(lines)

    def _deferred_quit(self) -> None:
        self._stop_recording()
        self.exit()

    def _stop_recording(self) -> None:
        if self.state not in (AppState.RECORDING, AppState.PAUSED):
            return

        self._tick_timer.pause()
        if hasattr(self, "_batch_timer"):
            self._batch_timer.pause()

        self.state = AppState.IDLE

        if self._transcriber is not None:
            self._transcriber.end_session()

        if self._audio_recorder is not None:
            try:
                self._audio_recorder.stop()
            except Exception as exc:
                log.exception("Failed to stop audio recorder")
                self._show_error(f"Could not stop audio recorder: {exc}")
            finally:
                self._audio_recorder = None

        if self._session is not None:
            try:
                self._session.finalize()
            except Exception as exc:
                log.exception("Failed to finalize session")
                self._show_error(f"Could not finalize session: {exc}")
            finally:
                self._session = None

    def update_live_preview(self, text: str) -> None:
        self._set_live_partial(text)

    def append_caption(self, text: str) -> None:
        self.query_one("#captions", RichLog).write(text)
