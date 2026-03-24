"""Textual TUI application — main entry point for the scarecrow UI."""

from __future__ import annotations

import contextlib
import logging
from enum import Enum, auto
from typing import TYPE_CHECKING, ClassVar

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.reactive import reactive
from textual.widgets import Footer, RichLog, Static

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


# ------------------------------------------------------------------
# Custom widgets
# ------------------------------------------------------------------

_STATE_STYLE: dict[AppState, tuple[str, str, str]] = {
    AppState.IDLE: ("IDLE", "dim", ""),
    AppState.RECORDING: ("REC", "bold white on dark_red", "\U0001f3a4"),
    AppState.PAUSED: ("PAUSED", "bold black on yellow", ""),
}


class InfoBar(Static):
    """Top bar: state + mic indicator, elapsed time, word count, batch countdown."""

    state: reactive[AppState] = reactive(AppState.IDLE)
    elapsed: reactive[int] = reactive(0)
    word_count: reactive[int] = reactive(0)
    batch_countdown: reactive[int] = reactive(BATCH_INTERVAL_SECONDS)

    def render(self) -> Text:
        label, style, icon = _STATE_STYLE[self.state]
        t = Text()
        t.append(f" {label} ", style=style)
        if icon:
            t.append(f" {icon}", style="")
        t.append("  ", style="")

        h = self.elapsed // 3600
        m = (self.elapsed % 3600) // 60
        s = self.elapsed % 60
        t.append(f"{h:02d}:{m:02d}:{s:02d}", style="bold")
        t.append("  ", style="")

        t.append(f"{self.word_count}", style="bold")
        t.append(" words", style="dim")
        t.append("  ", style="")

        if self.state in (AppState.RECORDING, AppState.PAUSED):
            t.append("batch ", style="dim")
            t.append(f"{self.batch_countdown}s", style="bold")

        return t


class ScarecrowApp(App[None]):
    """Scarecrow — always-recording TUI with live captions."""

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
        self._suppress_live: bool = False
        self._live_history: list[str] = []
        self._has_partial: bool = False

    def compose(self) -> ComposeResult:
        from scarecrow import config

        yield InfoBar(id="info-bar")
        yield Static(
            f"Transcript  [dim]({config.FINAL_MODEL} \u00b7 "
            f"every {BATCH_INTERVAL_SECONDS}s)[/dim]",
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
        yield RichLog(
            id="live-log",
            highlight=False,
            markup=False,
            wrap=True,
            min_width=0,
            auto_scroll=True,
        )
        yield Footer()

    def on_mount(self) -> None:
        self._tick_timer = self.set_interval(1, self._tick, pause=True)
        self._sync_info_bar()
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
        try:
            self.query_one("#captions", RichLog).write(
                f"[bold red]Error:[/bold red] {message}"
            )
        except Exception:
            log.error("UI error: %s", message)

    # ------------------------------------------------------------------
    # Timer / stats
    # ------------------------------------------------------------------

    def _tick(self) -> None:
        self._elapsed += 1
        self._batch_countdown = max(0, self._batch_countdown - 1)
        self._sync_info_bar()

    # ------------------------------------------------------------------
    # Info bar sync
    # ------------------------------------------------------------------

    def _sync_info_bar(self) -> None:
        try:
            bar = self.query_one(InfoBar)
        except Exception:
            return
        bar.state = self.state
        bar.elapsed = self._elapsed
        bar.word_count = self._word_count
        bar.batch_countdown = self._batch_countdown

    def watch_state(self, _new_state: AppState) -> None:
        self._sync_info_bar()

    # ------------------------------------------------------------------
    # Live pane — driven by RealtimeSTT callbacks (tiny.en, continuous)
    # ------------------------------------------------------------------

    def _safe_call(self, callback, *args) -> None:
        with contextlib.suppress(RuntimeError):
            self.call_from_thread(callback, *args)

    def _on_realtime_update(self, text: str) -> None:
        if not self._suppress_live and text:
            self._safe_call(self._update_live_partial, text)

    def _on_realtime_stabilized(self, text: str) -> None:
        if not self._suppress_live and text:
            self._safe_call(self._append_live, text)

    def _update_live(self, text: str) -> None:
        """System message — clears pane entirely."""
        live_log = self.query_one("#live-log", RichLog)
        live_log.clear()
        live_log.write(text)
        self._has_partial = False

    def _update_live_partial(self, text: str) -> None:
        """Show in-progress text (clears partial line, keeps history)."""
        live_log = self.query_one("#live-log", RichLog)
        # Remove the previous partial line (last line) and replace it
        if self._has_partial:
            live_log.clear()
            for line in self._live_history:
                live_log.write(line)
        live_log.write(text)
        self._has_partial = True

    def _append_live(self, text: str) -> None:
        """Append finalized text to live pane (permanent, scrollable)."""
        live_log = self.query_one("#live-log", RichLog)
        # Replace partial with finalized
        if self._has_partial:
            live_log.clear()
            for line in self._live_history:
                live_log.write(line)
        live_log.write(text)
        self._live_history.append(text)
        # Keep last 50 lines
        if len(self._live_history) > 50:
            self._live_history.pop(0)
        self._has_partial = False

    # ------------------------------------------------------------------
    # Transcript pane — batch transcription with medium.en every 30s
    # ------------------------------------------------------------------

    def _batch_transcribe(self) -> None:
        """Drain audio buffer and transcribe with medium.en in a worker."""
        log.debug("batch_transcribe called, state=%s", self.state)
        if self._audio_recorder is None or self._transcriber is None:
            log.debug("batch_transcribe: no recorder or transcriber")
            return

        audio = self._audio_recorder.drain_buffer()
        if audio is None or len(audio) == 0:
            log.debug("batch_transcribe: no audio in buffer")
            return

        log.debug(
            "batch_transcribe: got %d samples (%.1fs at %dHz)",
            len(audio),
            len(audio) / self._audio_recorder.sample_rate,
            self._audio_recorder.sample_rate,
        )

        batch_elapsed = self._elapsed
        self.run_worker(
            lambda: self._run_batch(audio, batch_elapsed),
            thread=True,
            name="batch-transcribe",
        )

    def _run_batch(self, audio, batch_elapsed: int) -> None:
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
            log.debug("Batch result: %r", text[:100] if text else "")
            if text.strip():
                self._safe_call(self._append_transcript, text.strip(), batch_elapsed)
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

    def _append_transcript(self, text: str, batch_elapsed: int | None = None) -> None:
        """Append batch-transcribed text to the transcript pane and file."""
        captions = self.query_one("#captions", RichLog)
        if self._session is not None:
            path = self._session.transcript_path
            elapsed = batch_elapsed if batch_elapsed is not None else self._elapsed
            h = elapsed // 3600
            m = (elapsed % 3600) // 60
            s = elapsed % 60
            ts = f"{h:02d}:{m:02d}:{s:02d}"
            divider = f"\u2500\u2500 {ts} \u00b7 {path} \u2500\u2500"
            captions.write(f"[dim]{divider}[/dim]")
            self._session.append_sentence(f"\n{divider}")
        captions.write(text)
        if self._session is not None:
            self._session.append_sentence(text)
        words = len(text.split())
        self._word_count += words
        self._sync_info_bar()

    def _write_pause_marker(self) -> None:
        """Write a 'Recording paused' marker to transcript pane and file."""
        h = self._elapsed // 3600
        m = (self._elapsed % 3600) // 60
        s = self._elapsed % 60
        ts = f"{h:02d}:{m:02d}:{s:02d}"
        marker = f"\u2500\u2500 {ts} \u00b7 Recording paused \u2500\u2500"
        self.query_one("#captions", RichLog).write(f"[dim]{marker}[/dim]")
        if self._session is not None:
            self._session.append_sentence(f"\n{marker}")

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _start_recording(self) -> None:
        from scarecrow import config

        if self.state is not AppState.IDLE:
            return
        if self._transcriber is None or not self._transcriber.is_ready:
            self._show_error("Transcriber not ready.")
            return

        self._session = Session()
        self._audio_recorder = AudioRecorder(
            output_path=self._session.audio_path,
            sample_rate=config.SAMPLE_RATE,
            on_audio=self._transcriber.feed_audio,
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

        self._transcriber.start()

        self._batch_timer = self.set_interval(
            BATCH_INTERVAL_SECONDS, self._on_batch_tick
        )

        self._elapsed = 0
        self._batch_countdown = BATCH_INTERVAL_SECONDS
        self._word_count = 0
        self._suppress_live = False
        self.state = AppState.RECORDING
        self._tick_timer.resume()
        self._update_live("Listening\u2026")

    def _on_batch_tick(self) -> None:
        """Called every BATCH_INTERVAL_SECONDS."""
        self._batch_countdown = BATCH_INTERVAL_SECONDS
        self._sync_info_bar()
        if self.state is AppState.RECORDING:
            self._batch_transcribe()
        elif self.state is AppState.PAUSED:
            self._write_pause_marker()

    def action_pause(self) -> None:
        if self.state is AppState.RECORDING:
            # Transcribe any buffered audio before pausing
            self._batch_transcribe()
            self.state = AppState.PAUSED
            self._suppress_live = True
            self._update_live("Paused")
            if self._audio_recorder is not None:
                self._audio_recorder.pause()
            self._write_pause_marker()
            # Timer keeps running — elapsed tracks total session time
        elif self.state is AppState.PAUSED:
            self.state = AppState.RECORDING
            self._suppress_live = False
            if self._audio_recorder is not None:
                self._audio_recorder.resume()
            self._update_live("Listening\u2026")
            # Reset batch countdown on resume for clean intervals
            self._batch_countdown = BATCH_INTERVAL_SECONDS
            self._sync_info_bar()

    def action_quit(self) -> None:
        self._update_live("Shutting down\u2026")
        self.set_timer(0.05, self._deferred_quit)

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
