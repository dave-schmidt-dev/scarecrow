"""Textual TUI application for Scarecrow."""

from __future__ import annotations

import contextlib
import logging
import threading
import time
from concurrent.futures import (
    Future,
    ThreadPoolExecutor,
)
from concurrent.futures import (
    TimeoutError as FuturesTimeoutError,
)
from datetime import datetime
from enum import Enum, auto
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from rich.markup import escape
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.css.query import NoMatches
from textual.reactive import reactive
from textual.widgets import Footer, Input, RichLog, Static

from scarecrow import config
from scarecrow.config import Config
from scarecrow.recorder import AudioRecorder
from scarecrow.session import Session
from scarecrow.transcriber import TranscriberBindings

if TYPE_CHECKING:
    from scarecrow.transcriber import Transcriber

log = logging.getLogger(__name__)


BATCH_INTERVAL_SECONDS = config.BATCH_INTERVAL
RICHLOG_MAX_LINES = 500


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
    peak_level: reactive[float] = reactive(0.0, always_update=True)

    def render(self) -> Text:
        label, style, icon = _STATE_STYLE[self.state]
        text = Text()
        text.append(f" {label} ", style=style)
        if icon:
            text.append(f" {icon}")
        if self.state is AppState.RECORDING:
            import math

            bars = " ▁▂▃▄▅▆▇█"
            raw = self.peak_level
            # Log scale: map ~0.005-0.3 range to 0-1
            if raw < 0.003:
                scaled = 0.0
            else:
                db = 20 * math.log10(max(raw, 1e-6))
                # -46dB (silence) to -10dB (loud speech) → 0.0 to 1.0
                scaled = max(0.0, min(1.0, (db + 46) / 36))
            idx = int(scaled * (len(bars) - 1))
            if scaled < 0.4:
                color = "green"
            elif scaled < 0.75:
                color = "yellow"
            else:
                color = "red"
            text.append(" ")
            text.append(bars[idx], style=color)
            if scaled < 0.15:
                label, lstyle = "quiet", "dim"
            elif scaled < 0.4:
                label, lstyle = "low  ", "green"
            elif scaled < 0.75:
                label, lstyle = "med  ", "yellow"
            else:
                label, lstyle = "HIGH ", f"bold {color}"
            text.append(f" {label}", style=lstyle)
        text.append("  ")

        h = self.elapsed // 3600
        m = (self.elapsed % 3600) // 60
        s = self.elapsed % 60
        text.append(f"{h:02d}:{m:02d}:{s:02d}", style="bold")
        text.append("  ")

        # Drop word count and batch countdown on narrow terminals
        width = self.size.width if self.size.width > 0 else 120
        if width >= 60:
            text.append(f"{self.word_count}", style="bold")
            text.append(" words", style="dim")
            text.append("  ")

        if self.state in (AppState.RECORDING, AppState.PAUSED) and width >= 50:
            label = "buf "
            text.append(label, style="dim")
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
        Binding("ctrl+p", "pause", "Pause/Resume", show=True),
        Binding("ctrl+q", "quit", "Quit", show=True),
    ]

    state: reactive[AppState] = reactive(AppState.IDLE)
    _elapsed: reactive[int] = reactive(0)
    _word_count: reactive[int] = reactive(0)
    _batch_countdown: reactive[int] = reactive(BATCH_INTERVAL_SECONDS)

    def __init__(
        self,
        transcriber: Transcriber | None = None,
        *,
        cfg: Config | None = None,
    ) -> None:
        super().__init__()
        self._cfg = cfg or config.config
        self._session: Session | None = None
        self._audio_recorder: AudioRecorder | None = None
        self._transcriber: Transcriber | None = transcriber
        self._status_message: str = ""
        self._status_is_error = False
        self._session_name: str = ""
        self._shutdown_summary = ""
        self._summary_path: Path | None = None
        self._batch_executor: ThreadPoolExecutor | None = None
        self._batch_futures: set[Future[str | None]] = set()
        self._batch_window_start: int = 0
        self._shutdown_lock = threading.RLock()
        self._ignore_batch_results = False
        self._note_counts: dict[str, int] = {"TASK": 0, "NOTE": 0, "CONTEXT": 0}
        self._recording_start_time: float | None = None
        self._disk_warn_shown: bool = False
        self._session_disk_warn_shown: bool = False
        self._circuit_breaker_shown: bool = False
        self._last_divider_elapsed: int = -self._cfg.DIVIDER_INTERVAL
        # Paragraph accumulator: join consecutive batch results on one block
        self._current_paragraph: str = ""
        self._paragraph_line_count: int = 0

    def compose(self) -> ComposeResult:
        yield InfoBar(id="info-bar")
        model_label = self._cfg.PARAKEET_MODEL.split("/")[-1]
        interval_label = "VAD"
        yield Static(
            f"Transcript  [dim]({model_label} · {interval_label})[/dim]",
            classes="pane-label",
        )
        yield RichLog(
            id="captions",
            highlight=True,
            markup=True,
            wrap=True,
            min_width=0,
        )
        startup_hint = "Starting\u2026"
        yield Static(
            startup_hint,
            id="notes-label",
            classes="pane-label",
        )
        yield Input(placeholder="Type a note...", id="note-input")
        yield Footer()

    _BANNER = (
        "[dim]"
        "        🎩\n"
        "       (°_°)\n"
        "    ──── | ────🎤\n"
        "        | |\n"
        "     Scarecrow v1.0[/dim]"
    )

    def on_mount(self) -> None:
        self._tick_timer = self.set_interval(1, self._tick, pause=True)
        self._sync_info_bar()
        with contextlib.suppress(NoMatches):
            self.query_one("#captions", RichLog).write(self._BANNER)
        self.query_one("#note-input", Input).focus()
        if not self._preflight_check():
            return
        self._start_recording()
        # Restore notes label
        with contextlib.suppress(NoMatches):
            label = self.query_one("#notes-label", Static)
            label.update(
                "Notes  [dim]"
                "(/t task  /c context  /mn name  /f flush  /help · Enter)"
                "[/dim]"
            )

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
            self._show_error("Batch transcriber not initialized.")
            return False

        return True

    _DEVICE_LOSS_THRESHOLD: ClassVar[float] = 3.0  # seconds without callback

    def _tick(self) -> None:
        if self._recording_start_time is not None:
            self._elapsed = int(time.monotonic() - self._recording_start_time)
        self._check_recorder_warnings()
        self._check_device_loss()
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
        bar.peak_level = (
            self._audio_recorder.peak_level if self._audio_recorder else 0.0
        )

    def watch_state(self, _new_state: AppState) -> None:
        self._sync_info_bar()

    def _current_state(self) -> AppState:
        return getattr(self, "_reactive_state", AppState.IDLE)

    def _set_status(self, message: str, *, error: bool = False) -> None:
        if not message and not error and self._session_name:
            message = f"Session: {self._session_name}"
        self._status_message = message
        self._status_is_error = error
        self._sync_info_bar()

    def _warn_transcript(self, message: str) -> None:
        """Write a WARNING line to both the RichLog and session transcript."""
        timestamp = datetime.now().strftime("%H:%M:%S")
        styled_line = (
            f"[bold yellow][WARNING][/bold yellow]"
            f" [dim]{timestamp}[/dim] \u2014 {message}"
        )
        self._current_paragraph = ""
        self._paragraph_line_count = 0
        with contextlib.suppress(NoMatches):
            self.query_one("#captions", RichLog).write(styled_line)
        if self._session is not None:
            self._session.append_event(
                {
                    "type": "warning",
                    "elapsed": self._elapsed,
                    "timestamp": datetime.now().isoformat(timespec="seconds"),
                    "text": message,
                }
            )

    def _check_recorder_warnings(self) -> None:
        """Poll recorder and session for warnings; write to transcript once."""
        if self._audio_recorder is not None:
            warning = self._audio_recorder._last_warning
            if warning:
                self._audio_recorder._last_warning = None
                self._warn_transcript(warning)

            if self._audio_recorder._disk_write_failed and not self._disk_warn_shown:
                self._disk_warn_shown = True
                self._warn_transcript("Audio file write failed \u2014 disk may be full")

        if (
            self._session is not None
            and self._session.write_failed
            and not self._session_disk_warn_shown
        ):
            self._session_disk_warn_shown = True
            self._warn_transcript("Transcript write failed \u2014 disk may be full")

    def _check_device_loss(self) -> None:
        """Detect audio device change or loss and restart stream."""
        if self.state is not AppState.RECORDING:
            return
        if self._audio_recorder is None:
            return
        device_changed = self._audio_recorder.default_device_changed
        stale = self._audio_recorder.seconds_since_last_callback
        if not device_changed and stale < self._DEVICE_LOSS_THRESHOLD:
            return
        if device_changed:
            log.warning("Default audio device changed — restarting stream")
            self._warn_transcript("Audio device changed — switching to new default mic")
        else:
            log.warning(
                "No audio callback for %.1fs — attempting stream restart",
                stale,
            )
            self._warn_transcript("Audio device lost — reconnecting to default mic")
        try:
            self._audio_recorder.restart_stream()
            self._set_status("Mic reconnected")
        except Exception as exc:
            log.exception("Failed to restart audio stream")
            self._set_status(f"Mic reconnect failed: {exc}", error=True)

    def _handle_flush(self) -> None:
        """Force-flush the audio buffer immediately."""
        with contextlib.suppress(NoMatches):
            self.query_one("#note-input", Input).value = ""
        if self.state is not AppState.RECORDING:
            return
        if self._audio_recorder is None or self._transcriber is None:
            return

        self._reap_batch_futures()
        if self._batch_futures:
            self._set_status("Batch busy; flush queued for next cycle.")
            return

        audio = self._audio_recorder.drain_buffer()
        if audio is not None and len(audio) > 0:
            batch_elapsed = self._batch_window_start
            self._batch_window_start = self._elapsed
            self._submit_batch_transcription(audio, batch_elapsed)

    def _handle_meeting_name(self, raw: str) -> None:
        """Rename the current session with a meeting name."""
        with contextlib.suppress(NoMatches):
            self.query_one("#note-input", Input).value = ""

        # Strip the command prefix
        name = ""
        for prefix in ("/mn ", "/meeting "):
            if raw.lower().startswith(prefix):
                name = raw[len(prefix) :].strip()
                break
        else:
            return

        if not name:
            return

        if self._session is None:
            self._set_status("No active session to rename.", error=True)
            return

        try:
            self._session.rename(name)
            self._session_name = name
            self._set_status(f"Session: {name}")
            # Update the audio recorder's output path so stop() returns correct path
            if self._audio_recorder is not None:
                self._audio_recorder._output_path = self._session.audio_path
        except Exception as exc:
            log.exception("Failed to rename session")
            self._show_error(f"Could not rename session: {exc}")

    def _show_help(self) -> None:
        """Show inline help in the transcript pane."""
        with contextlib.suppress(NoMatches):
            self.query_one("#note-input", Input).value = ""
        help_text = (
            "[bold]Commands:[/bold]\n"
            "  /task, /t [dim]<text>[/dim]   "
            "Add a task note\n"
            "  /context, /c [dim]<text>[/dim]  "
            "Add background context (spelling, names — aids summary, not displayed)\n"
            "  /mn [dim]<name>[/dim]         "
            "Name this session\n"
            "  /flush, /f          "
            "Force-flush the audio buffer now\n"
            "  /help, /h, ?        "
            "Show this message\n"
            "\n"
            "[bold]Keybindings:[/bold]\n"
            "  Ctrl+P              Pause / resume\n"
            "  Ctrl+Q              Quit\n"
            "  Enter               Submit note"
        )
        with contextlib.suppress(NoMatches):
            self.query_one("#captions", RichLog).write(help_text)

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

    def _bind_callbacks(self) -> None:
        if self._transcriber is not None:
            self._transcriber.bind(
                TranscriberBindings(
                    on_batch_result=self._on_batch_result,
                    on_error=self._on_transcriber_error,
                )
            )

    def _on_batch_result(self, text: str, batch_elapsed: int) -> None:
        if self._ignore_batch_results:
            log.debug("Ignoring late batch result during shutdown")
            return
        self._post_to_ui(self._append_transcript, text, batch_elapsed)

    def _on_transcriber_error(self, source: str, message: str) -> None:
        self._post_to_ui(self._show_error, f"{source}: {message}")

    def _reap_batch_futures(self) -> None:
        alive: set[Future[str | None]] = set()
        for future in self._batch_futures:
            if not future.done():
                alive.add(future)
                continue
            try:
                future.result(timeout=0)
            except Exception as exc:
                log.exception("Batch worker failed")
                self._warn_transcript(f"Batch transcription failed: {exc}")
        self._batch_futures = alive

    def _ensure_batch_executor(self) -> ThreadPoolExecutor:
        if self._batch_executor is None:
            self._batch_executor = ThreadPoolExecutor(
                max_workers=1,
                thread_name_prefix="batch-transcribe",
            )
        return self._batch_executor

    def _submit_batch_transcription(self, audio, batch_elapsed: int) -> bool:
        if self._transcriber is None or len(audio) == 0:
            return False

        # Circuit breaker: stop submitting after repeated consecutive failures
        if self._transcriber.consecutive_failures >= self._MAX_CONSECUTIVE_FAILURES:
            if not self._circuit_breaker_shown:
                self._circuit_breaker_shown = True
                self._warn_transcript(
                    "Transcription unavailable — audio is still recording. "
                    "Restart Scarecrow to retry."
                )
                self._set_status("Transcription unavailable", error=True)
            return False

        self._reap_batch_futures()
        if self._batch_futures:
            log.warning(
                "Skipping batch tick while previous batch "
                "transcription is still running"
            )
            self._set_status("Batch busy; carrying audio into the next window.")
            return False

        future = self._ensure_batch_executor().submit(
            self._transcriber.transcribe_batch,
            audio,
            batch_elapsed,
        )
        self._batch_futures.add(future)
        return True

    def _prune_richlog(self) -> None:
        """Remove oldest lines from RichLog if it exceeds RICHLOG_MAX_LINES."""
        try:
            captions = self.query_one("#captions", RichLog)
        except NoMatches:
            return
        overflow = len(captions.lines) - RICHLOG_MAX_LINES
        if overflow > 0:
            del captions.lines[:overflow]
            captions._line_cache.clear()
            # Paragraph tracking is invalidated by pruning
            self._current_paragraph = ""
            self._paragraph_line_count = 0

    def _transcript_divider(self, elapsed: int, path) -> str:
        h = elapsed // 3600
        m = (elapsed % 3600) // 60
        s = elapsed % 60
        ts = f"{h:02d}:{m:02d}:{s:02d}"
        return f"── {ts} · {path} ──"

    def _record_transcript(
        self,
        text: str,
        batch_elapsed: int | None = None,
        *,
        include_ui: bool = True,
    ) -> None:
        elapsed = batch_elapsed if batch_elapsed is not None else self._elapsed
        divider_gap = elapsed - self._last_divider_elapsed
        show_divider = divider_gap >= self._cfg.DIVIDER_INTERVAL
        divider = None
        if show_divider and self._session is not None:
            divider = self._transcript_divider(elapsed, self._session.transcript_path)
            self._last_divider_elapsed = elapsed

        if include_ui:
            try:
                captions = self.query_one("#captions", RichLog)
            except NoMatches:
                captions = None
            if captions is not None:
                if divider is not None:
                    # Freeze current paragraph, write divider, start fresh
                    self._current_paragraph = ""
                    self._paragraph_line_count = 0
                    captions.write(f"[dim]{divider}[/dim]")

                # Append to current paragraph and replace in RichLog
                if self._current_paragraph:
                    self._current_paragraph += " " + escape(text)
                else:
                    self._current_paragraph = escape(text)

                # Remove previous paragraph lines, then write updated block
                if self._paragraph_line_count > 0:
                    del captions.lines[-self._paragraph_line_count :]
                    captions._line_cache.clear()
                before = len(captions.lines)
                captions.write(self._current_paragraph)
                self._paragraph_line_count = len(captions.lines) - before

        if self._session is not None:
            if divider is not None:
                self._session.append_event(
                    {
                        "type": "divider",
                        "elapsed": elapsed,
                        "timestamp": datetime.now().isoformat(timespec="seconds"),
                    }
                )
            self._session.append_event(
                {
                    "type": "transcript",
                    "elapsed": elapsed,
                    "timestamp": datetime.now().isoformat(timespec="seconds"),
                    "text": text,
                }
            )

        self._word_count += len(text.split())
        if include_ui:
            self._set_status("")
            self._sync_info_bar()
            self._prune_richlog()

    def _flush_final_batch(self, *, include_ui: bool = True) -> None:
        if self._audio_recorder is None or self._transcriber is None:
            return

        audio = self._audio_recorder.drain_buffer()
        if audio is None or len(audio) == 0:
            return

        batch_elapsed = self._batch_window_start
        try:
            text = self._transcriber.transcribe_batch(
                audio,
                batch_elapsed,
                emit_callback=False,
                max_retries=0,
            )
        except Exception as exc:
            log.exception("Final batch transcription failed during shutdown")
            if include_ui:
                self._show_error(f"Final batch transcription failed: {exc}")
            return
        if text:
            self._record_transcript(
                text,
                batch_elapsed,
                include_ui=include_ui,
            )

    def _wait_for_batch_workers(self) -> tuple[bool, list[str]]:
        timed_out = False
        captured: list[str] = []
        for future in list(self._batch_futures):
            try:
                result = future.result(timeout=10)
                if result:
                    captured.append(result)
            except FuturesTimeoutError:
                timed_out = True
                log.warning(
                    "Batch worker did not finish within 10s during shutdown; "
                    "proceeding without it."
                )
            except Exception:
                log.exception("Batch worker raised during shutdown")
        self._batch_futures.clear()
        if timed_out and self._batch_executor is not None:
            self._ignore_batch_results = True
            self._batch_executor.shutdown(wait=False, cancel_futures=False)
            self._batch_executor = None
        return not timed_out, captured

    def _vad_transcribe(self) -> None:
        """Drain audio at silence boundaries (parakeet backend)."""
        if self._audio_recorder is None or self._transcriber is None:
            return

        self._reap_batch_futures()
        if self._batch_futures:
            return

        audio = self._audio_recorder.drain_to_silence()
        if audio is None or len(audio) == 0:
            return

        batch_elapsed = self._batch_window_start
        self._batch_window_start = self._elapsed
        self._submit_batch_transcription(audio, batch_elapsed)

    def _append_transcript(self, text: str, batch_elapsed: int | None = None) -> None:
        self._record_transcript(text, batch_elapsed)

    def _write_pause_marker(self) -> None:
        h = self._elapsed // 3600
        m = (self._elapsed % 3600) // 60
        s = self._elapsed % 60
        ts = f"{h:02d}:{m:02d}:{s:02d}"
        marker = f"── {ts} · Recording paused ──"
        self._current_paragraph = ""
        self._paragraph_line_count = 0
        try:
            captions = self.query_one("#captions", RichLog)
            captions.write(f"[dim]{marker}[/dim]")
        except NoMatches:
            pass
        if self._session is not None:
            self._session.append_event(
                {
                    "type": "pause",
                    "elapsed": self._elapsed,
                    "timestamp": datetime.now().isoformat(timespec="seconds"),
                }
            )

    def _start_recording(self) -> None:
        if self.state is not AppState.IDLE:
            return
        if self._transcriber is None or not self._transcriber.is_ready:
            self._show_error("Transcriber not ready.")
            return

        self._bind_callbacks()
        self._ignore_batch_results = False
        try:
            self._session = Session(base_dir=self._cfg.DEFAULT_RECORDINGS_DIR)
        except Exception as exc:
            log.exception("Failed to create recording session directory")
            self._show_error(f"Could not create session: {exc}")
            return

        self._audio_recorder = AudioRecorder(
            output_path=self._session.audio_path,
            sample_rate=self._cfg.RECORDING_SAMPLE_RATE,
            cfg=self._cfg,
        )

        try:
            self._audio_recorder.start()
        except Exception as exc:
            log.exception("Failed to start recording session")
            try:
                self._audio_recorder.stop()
            except Exception:
                log.exception("Failed to unwind audio recorder after startup error")
            try:
                self._session.finalize()
            except Exception:
                log.exception("Failed to finalize session after startup error")
            self._show_error(f"Could not start recording session: {exc}")
            self._audio_recorder = None
            self._session = None
            return

        self._batch_timer = self.set_interval(
            self._cfg.VAD_POLL_INTERVAL_MS / 1000,
            self._on_vad_poll,
        )
        self._recording_start_time = time.monotonic()
        self._elapsed = 0
        self._batch_window_start = 0
        self._batch_countdown = BATCH_INTERVAL_SECONDS
        self._word_count = 0
        self.state = AppState.RECORDING
        self._tick_timer.resume()
        self._set_status("")

        # Show session header in transcript pane
        with contextlib.suppress(NoMatches):
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.query_one("#captions", RichLog).write(
                f"[dim]Session Start: {ts}[/dim]"
            )

        if self._session is not None:
            self._session.append_event(
                {
                    "type": "recording_start",
                    "elapsed": 0,
                    "timestamp": datetime.now().isoformat(timespec="seconds"),
                }
            )

    def _on_vad_poll(self) -> None:
        """Poll for silence boundaries (parakeet backend)."""
        if self.state is AppState.RECORDING:
            self._vad_transcribe()
        # Always update buffer age display
        if self._audio_recorder is not None:
            buf_s = int(self._audio_recorder.buffer_seconds)
            self._batch_countdown = buf_s
            self._sync_info_bar()

    def action_pause(self) -> None:
        if self.state is AppState.RECORDING:
            self._vad_transcribe()
            self.state = AppState.PAUSED
            if self._audio_recorder is not None:
                # Force-flush any remaining pre-pause audio that VAD couldn't drain
                # (e.g. speech without a silence boundary). This prevents stale audio
                # from contaminating post-resume transcription.
                self._reap_batch_futures()
                if not self._batch_futures:
                    audio = self._audio_recorder.drain_buffer()
                    if audio is not None and len(audio) > 0:
                        batch_elapsed = self._batch_window_start
                        self._batch_window_start = self._elapsed
                        self._submit_batch_transcription(audio, batch_elapsed)
                else:
                    # Batch busy — discard rather than contaminate post-resume
                    self._audio_recorder.drain_buffer()
                try:
                    self._audio_recorder.pause()
                except Exception as exc:
                    log.exception("Failed to pause audio recorder")
                    self._set_status(f"Could not pause recorder: {exc}", error=True)
            self._set_status("Paused")
            self._write_pause_marker()
            return

        if self.state is AppState.PAUSED:
            self.state = AppState.RECORDING
            if self._audio_recorder is not None:
                try:
                    self._audio_recorder.resume()
                except Exception as exc:
                    log.exception("Failed to resume audio recorder")
                    self._set_status(f"Could not resume recorder: {exc}", error=True)
            # Write resume event to transcript
            if self._session is not None:
                self._session.append_event(
                    {
                        "type": "resume",
                        "elapsed": self._elapsed,
                        "timestamp": datetime.now().isoformat(timespec="seconds"),
                    }
                )
            self._batch_countdown = BATCH_INTERVAL_SECONDS
            self._last_divider_elapsed = -self._cfg.DIVIDER_INTERVAL
            self._set_status("")
            self._sync_info_bar()

    def action_quit(self) -> None:
        self._shutdown_summary = self._collect_shutdown_metrics()
        self._set_status("Shutting down…")
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
        self.cleanup_after_exit(include_ui=True)

    def cleanup_after_exit(self, *, include_ui: bool = False) -> None:
        with self._shutdown_lock:
            has_recording_state = self._current_state() in (
                AppState.RECORDING,
                AppState.PAUSED,
            )
            has_open_resources = (
                has_recording_state
                or self._audio_recorder is not None
                or self._session is not None
                or bool(self._batch_futures)
            )
            has_active_transcriber = (
                self._transcriber is not None and self._transcriber.is_ready
            )
            needs_cleanup = has_open_resources or has_active_transcriber
            if not needs_cleanup:
                return

            # Capture session_dir before any step can clear _session.
            session_dir = self._session.session_dir if self._session else None

            steps = [
                ("pause_timers", self._cleanup_pause_timers),
                ("stop_recorder", lambda: self._cleanup_stop_recorder(include_ui)),
                ("flush_audio", lambda: self._cleanup_flush_audio(include_ui)),
                ("clear_recorder", self._cleanup_clear_recorder),
                ("shutdown_executor", self._cleanup_shutdown_executor),
                (
                    "shutdown_transcriber",
                    lambda: self._cleanup_shutdown_transcriber(include_ui),
                ),
                ("session_metrics", self._cleanup_session_metrics),
                ("session_end_header", self._cleanup_session_end_header),
                ("compress_audio", self._cleanup_compress_audio),
                (
                    "session_finalize",
                    lambda: self._cleanup_session_finalize(include_ui),
                ),
                ("auto_summarize", lambda: self._cleanup_auto_summarize(session_dir)),
                ("reset_state", self._cleanup_reset_state),
            ]
            for name, step in steps:
                try:
                    step()
                except Exception:
                    log.exception("Cleanup step %s failed", name)

    def _cleanup_pause_timers(self) -> None:
        if hasattr(self, "_tick_timer"):
            self._tick_timer.pause()
        if hasattr(self, "_batch_timer"):
            self._batch_timer.pause()

    def _cleanup_stop_recorder(self, include_ui: bool) -> None:
        if self._audio_recorder is None:
            return
        try:
            self._audio_recorder.stop()
        except Exception as exc:
            log.exception("Failed to stop audio recorder")
            if include_ui:
                self._show_error(f"Could not stop audio recorder: {exc}")

    def _cleanup_flush_audio(self, include_ui: bool) -> None:
        """Wait for in-flight batch workers and flush the final audio buffer.

        Catches BaseException so that KeyboardInterrupt during flush does not
        abort the remaining cleanup steps — the finally clause in the caller's
        loop still clears _audio_recorder via _cleanup_clear_recorder.
        """
        try:
            self._ignore_batch_results = True
            batch_workers_finished, captured = self._wait_for_batch_workers()
            for text in captured:
                self._record_transcript(text, include_ui=include_ui)
            if batch_workers_finished:
                self._flush_final_batch(include_ui=include_ui)
            else:
                log.warning(
                    "Skipping final batch flush because a batch worker "
                    "is still running after shutdown timeout"
                )
                if include_ui:
                    self._show_error(
                        "Batch worker timed out during shutdown; "
                        "skipping final transcript flush."
                    )
        except BaseException as exc:
            if isinstance(exc, KeyboardInterrupt):
                log.warning("KeyboardInterrupt during batch flush; continuing cleanup")
            else:
                log.exception(
                    "Failed while flushing batch transcription during shutdown"
                )
            if include_ui and isinstance(exc, Exception):
                self._show_error(f"Could not flush final transcript batch: {exc}")

    def _cleanup_clear_recorder(self) -> None:
        self._audio_recorder = None

    def _cleanup_shutdown_executor(self) -> None:
        if self._batch_executor is not None:
            self._batch_executor.shutdown(wait=False, cancel_futures=False)
            self._batch_executor = None

    def _cleanup_shutdown_transcriber(self, include_ui: bool) -> None:
        if self._transcriber is None or not self._transcriber.is_ready:
            return
        try:
            self._transcriber.shutdown(timeout=5)
        except Exception as exc:
            log.exception("Failed to shut down transcriber")
            if include_ui:
                self._show_error(f"Could not shut down transcriber: {exc}")

    def _cleanup_session_metrics(self) -> None:
        if self._session is None:
            return
        self._session.append_event(
            {
                "type": "session_metrics",
                "elapsed": self._elapsed,
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "word_count": self._word_count,
            }
        )

    def _cleanup_session_end_header(self) -> None:
        if self._session is None:
            return
        self._session.write_end_header()

    def _cleanup_compress_audio(self) -> None:
        if self._session is not None:
            self._session.compress_audio()

    def _cleanup_session_finalize(self, include_ui: bool) -> None:
        if self._session is None:
            return
        try:
            self._session.finalize()
        except Exception as exc:
            log.exception("Failed to finalize session")
            if include_ui:
                self._show_error(f"Could not finalize session: {exc}")
        finally:
            self._session = None

    def _cleanup_auto_summarize(self, session_dir) -> None:
        if session_dir is None:
            return
        print("  Generating summary…", flush=True)
        from scarecrow.summarizer import summarize_session

        result = summarize_session(
            session_dir, obsidian_dir=self._cfg.OBSIDIAN_VAULT_DIR
        )
        if result:
            log.info("Summary: %s", result)
            self._summary_path = result

    def _cleanup_reset_state(self) -> None:
        try:
            self.state = AppState.IDLE
        except Exception:
            self._reactive_state = AppState.IDLE

    _MAX_CONSECUTIVE_FAILURES: ClassVar[int] = 3

    _NOTE_PREFIXES: ClassVar[dict[str, str]] = {
        "/task": "TASK",
        "/t": "TASK",
        "/context": "CONTEXT",
        "/c": "CONTEXT",
    }

    def _submit_note(self) -> None:
        """Read the note input, write to RichLog and transcript, then clear input."""
        try:
            input_widget = self.query_one("#note-input", Input)
        except NoMatches:
            return
        raw = input_widget.value.strip()
        if not raw:
            return

        # Parse optional prefix: /task, /t
        tag = "NOTE"
        for prefix, prefix_tag in self._NOTE_PREFIXES.items():
            if raw.lower().startswith(prefix + " "):
                tag = prefix_tag
                raw = raw[len(prefix) + 1 :].strip()
                break
            if raw.lower() == prefix:
                tag = prefix_tag
                raw = ""
                break

        if not raw:
            input_widget.value = ""
            return

        self._current_paragraph = ""
        self._paragraph_line_count = 0

        timestamp = datetime.now().strftime("%H:%M:%S")
        file_line = f"[{tag}] {timestamp} -- {raw}"
        escaped_raw = escape(raw)
        styled_line = (
            f"[bold cyan][{tag}][/bold cyan]"
            f" [dim]{timestamp}[/dim] \u2014 {escaped_raw}"
        )

        try:
            self.query_one("#captions", RichLog).write(styled_line)
        except NoMatches:
            log.error("Note pane unavailable: %s", file_line)

        if self._session is not None:
            self._session.append_event(
                {
                    "type": "note",
                    "tag": tag,
                    "elapsed": self._elapsed,
                    "timestamp": datetime.now().isoformat(timespec="seconds"),
                    "text": raw,
                }
            )

        self._note_counts[tag] = self._note_counts.get(tag, 0) + 1
        self._word_count += len(raw.split())
        self._sync_info_bar()
        self._prune_richlog()
        self._update_context_display()
        input_widget.value = ""

    def _update_context_display(self) -> None:
        try:
            display = self.query_one("#context-display", Static)
        except NoMatches:
            return
        parts = []
        for label, key in [
            ("Tasks", "TASK"),
            ("Notes", "NOTE"),
            ("Context", "CONTEXT"),
        ]:
            count = self._note_counts.get(key, 0)
            if count > 0:
                parts.append(f"{label}: {count}")
        if parts:
            display.update(" · ".join(parts))
            display.display = True
        else:
            display.update("")
            display.display = False

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "note-input":
            return
        raw = event.input.value.strip()
        lower = raw.lower()
        if lower in ("/help", "/h", "?"):
            self._show_help()
            return
        if lower in ("/flush", "/f"):
            self._handle_flush()
            return
        if lower.startswith("/mn ") or lower.startswith("/meeting "):
            self._handle_meeting_name(raw)
            return
        self._submit_note()

    def on_unmount(self) -> None:
        if self._batch_executor is not None:
            self._batch_executor.shutdown(wait=True, cancel_futures=False)
            self._batch_executor = None
