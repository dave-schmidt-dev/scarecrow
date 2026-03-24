"""Textual TUI application — main entry point for the scarecrow UI."""

from __future__ import annotations

import contextlib
import logging
import math
from enum import Enum, auto
from typing import TYPE_CHECKING, ClassVar

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.color import Color
from textual.reactive import reactive
from textual.widgets import Footer, RichLog, Sparkline, Static

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

_STATE_STYLE: dict[AppState, tuple[str, str]] = {
    AppState.IDLE: ("IDLE", "dim"),
    AppState.RECORDING: ("REC", "bold white on dark_red"),
    AppState.PAUSED: ("PAUSED", "bold black on yellow"),
}


class InfoBar(Static):
    """Top bar: state, elapsed time, word count, next batch countdown."""

    state: reactive[AppState] = reactive(AppState.IDLE)
    elapsed: reactive[int] = reactive(0)
    word_count: reactive[int] = reactive(0)
    batch_countdown: reactive[int] = reactive(BATCH_INTERVAL_SECONDS)

    def render(self) -> Text:
        label, style = _STATE_STYLE[self.state]
        t = Text()
        t.append(f" {label} ", style=style)
        t.append("  ", style="")

        h = self.elapsed // 3600
        m = (self.elapsed % 3600) // 60
        s = self.elapsed % 60
        t.append(f"{h:02d}:{m:02d}:{s:02d}", style="bold")
        t.append("  ", style="")

        t.append(f"{self.word_count}", style="bold")
        t.append(" words", style="dim")
        t.append("  ", style="")

        if self.state is AppState.RECORDING:
            t.append("batch ", style="dim")
            t.append(f"{self.batch_countdown}s", style="bold")

        return t


class AudioMeter(Static):
    """Simple text-based audio level indicator using dB scale."""

    level: reactive[float] = reactive(0.0)

    def render(self) -> Text:
        t = Text()
        t.append(" mic ", style="dim")
        # Convert linear 0-1 to dB-ish scale for useful visual range.
        # -60 dB (silence) → 0%, -6 dB (loud) → 100%
        if self.level > 0.0:
            db = 20 * math.log10(max(self.level, 1e-6))
            # Map -60..0 dB to 0..1
            normalized = max(0.0, min(1.0, (db + 60) / 60))
        else:
            normalized = 0.0
        bars = int(normalized * 20)
        filled = "\u2588" * bars
        empty = "\u2591" * (20 - bars)
        if normalized > 0.85:
            t.append(filled, style="bold red")
        elif normalized > 0.6:
            t.append(filled, style="bold yellow")
        else:
            t.append(filled, style="bold green")
        t.append(empty, style="dim")
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
    _audio_level: reactive[float] = reactive(0.0)

    def __init__(self, transcriber: Transcriber | None = None) -> None:
        super().__init__()
        self._session: Session | None = None
        self._audio_recorder: AudioRecorder | None = None
        self._transcriber: Transcriber | None = transcriber
        self._suppress_live: bool = False
        self._audio_history: list[float] = [0.0] * 40

    def compose(self) -> ComposeResult:
        from scarecrow import config

        yield InfoBar(id="info-bar")
        yield AudioMeter(id="audio-meter")
        yield Sparkline(
            self._audio_history,
            id="audio-spark",
            min_color=Color.parse("rgb(60,60,60)"),
            max_color=Color.parse("rgb(80,180,80)"),
        )
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
        self._level_timer = self.set_interval(0.5, self._update_audio_level, pause=True)
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
        self.query_one("#captions", RichLog).write(
            f"[bold red]Error:[/bold red] {message}"
        )

    # ------------------------------------------------------------------
    # Timer / stats
    # ------------------------------------------------------------------

    def _tick(self) -> None:
        self._elapsed += 1
        self._batch_countdown = max(0, self._batch_countdown - 1)
        self._sync_info_bar()

    def _update_audio_level(self) -> None:
        """Sample current audio level from the recorder buffer."""
        if self._audio_recorder is None or not self._audio_recorder.is_recording:
            return
        level = self._audio_recorder.peak_level
        self._audio_level = level
        meter = self.query_one(AudioMeter)
        if meter.level != level:
            meter.level = level
        self._audio_history.append(level)
        if len(self._audio_history) > 40:
            self._audio_history.pop(0)
        self.query_one("#audio-spark", Sparkline).refresh()

    # ------------------------------------------------------------------
    # Info bar sync
    # ------------------------------------------------------------------

    def _sync_info_bar(self) -> None:
        bar = self.query_one(InfoBar)
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
        log.debug("batch_transcribe called, state=%s", self.state)
        if self._audio_recorder is None or self._transcriber is None:
            log.debug("batch_transcribe: no recorder or transcriber")
            return
        if self.state not in (AppState.RECORDING, AppState.PAUSED):
            log.debug("batch_transcribe: wrong state %s", self.state)
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

        # Capture elapsed time now (at dispatch), not when result arrives
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
        # Divider between batches showing transcript path
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

        assert self._transcriber.recorder is not None
        self._transcriber.recorder.start()
        self._level_timer.resume()

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
        """Called every BATCH_INTERVAL_SECONDS — reset countdown, run batch."""
        self._batch_countdown = BATCH_INTERVAL_SECONDS
        self._sync_info_bar()
        self._batch_transcribe()

    def action_pause(self) -> None:
        if self.state is AppState.RECORDING:
            self.state = AppState.PAUSED
            self._tick_timer.pause()
            self._suppress_live = True
            if self._audio_recorder is not None:
                self._audio_recorder.pause()
        elif self.state is AppState.PAUSED:
            self.state = AppState.RECORDING
            self._tick_timer.resume()
            self._suppress_live = False
            if self._audio_recorder is not None:
                self._audio_recorder.resume()

    def action_quit(self) -> None:
        self._update_live("Shutting down\u2026")
        # Defer stop so the live pane renders "Shutting down" first
        self.set_timer(0.05, self._deferred_quit)

    def _deferred_quit(self) -> None:
        self._stop_recording()
        self.exit()

    def _stop_recording(self) -> None:
        if self.state not in (AppState.RECORDING, AppState.PAUSED):
            return

        self._tick_timer.pause()
        self._level_timer.pause()
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
