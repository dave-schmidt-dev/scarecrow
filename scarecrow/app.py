"""Textual TUI application for Scarecrow."""

from __future__ import annotations

import contextlib
import logging
import threading
from concurrent.futures import (
    Future,
    ThreadPoolExecutor,
)
from concurrent.futures import (
    TimeoutError as FuturesTimeoutError,
)
from datetime import datetime
from enum import Enum, auto
from typing import TYPE_CHECKING, ClassVar

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.css.query import NoMatches
from textual.reactive import reactive
from textual.widgets import Footer, Input, RichLog, Static

from scarecrow import config
from scarecrow.recorder import AudioRecorder
from scarecrow.session import Session
from scarecrow.transcriber import TranscriberBindings

if TYPE_CHECKING:
    from scarecrow.transcriber import Transcriber

log = logging.getLogger(__name__)

BATCH_INTERVAL_SECONDS = 15


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
                text.append(" quiet", style="dim")
            elif scaled >= 0.75:
                text.append(" LOUD", style=f"bold {color}")
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
    ) -> None:
        super().__init__()
        self._session: Session | None = None
        self._audio_recorder: AudioRecorder | None = None
        self._transcriber: Transcriber | None = transcriber
        self._status_message: str = ""
        self._status_is_error = False
        self._shutdown_summary = ""
        self._batch_executor: ThreadPoolExecutor | None = None
        self._batch_futures: set[Future[str | None]] = set()
        self._batch_window_start: int = 0
        self._shutdown_lock = threading.RLock()
        self._ignore_batch_results = False
        self._context_entries: list[str] = []
        self._awaiting_context: bool = True
        self._previous_batch_tail: str = ""
        self._note_counts: dict[str, int] = {"CONTEXT": 0, "TASK": 0, "NOTE": 0}

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
        yield Static("", id="context-display")
        yield Static(
            "Context (e.g. Alice, React, Q4 planning) or Enter to start",
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
        bar.peak_level = (
            self._audio_recorder.peak_level if self._audio_recorder else 0.0
        )

    def watch_state(self, _new_state: AppState) -> None:
        self._sync_info_bar()

    def _current_state(self) -> AppState:
        return getattr(self, "_reactive_state", AppState.IDLE)

    def _set_status(self, message: str, *, error: bool = False) -> None:
        self._status_message = message
        self._status_is_error = error
        self._sync_info_bar()

    def _show_help(self) -> None:
        """Show inline help in the transcript pane."""
        with contextlib.suppress(NoMatches):
            self.query_one("#note-input", Input).value = ""
        help_text = (
            "[bold]Commands:[/bold]\n"
            "  /task, /t [dim]<text>[/dim]   "
            "Add a task note\n"
            "  /context [dim]<terms>[/dim]   "
            "Add context terms for accuracy\n"
            "  /clear              "
            "Clear context & reset\n"
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
        if source == "audio":
            self._post_to_ui(lambda: self._set_status(message, error=True))
        else:
            self._post_to_ui(self._show_error, f"{source}: {message}")

    def _reap_batch_futures(self) -> None:
        self._batch_futures = {
            future for future in self._batch_futures if not future.done()
        }

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

        self._reap_batch_futures()
        if self._batch_futures:
            log.warning(
                "Skipping batch tick while previous batch "
                "transcription is still running"
            )
            self._set_status("Batch busy; carrying audio into the next window.")
            return False

        prompt = self._build_initial_prompt()
        future = self._ensure_batch_executor().submit(
            self._transcriber.transcribe_batch,
            audio,
            batch_elapsed,
            initial_prompt=prompt,
        )
        self._batch_futures.add(future)
        return True

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
        divider = None
        if self._session is not None:
            divider = self._transcript_divider(elapsed, self._session.transcript_path)

        if include_ui:
            try:
                captions = self.query_one("#captions", RichLog)
            except NoMatches:
                captions = None
            if captions is not None:
                if divider is not None:
                    captions.write(f"[dim]{divider}[/dim]")
                captions.write(text)

        if self._session is not None:
            if divider is not None:
                self._session.append_sentence(f"\n{divider}")
            self._session.append_sentence(text)

        self._word_count += len(text.split())
        if include_ui:
            self._set_status(
                "Listening…" if self._current_state() is AppState.RECORDING else ""
            )
            self._sync_info_bar()

    def _flush_final_batch(self, *, include_ui: bool = True) -> None:
        if self._audio_recorder is None or self._transcriber is None:
            return

        audio = self._audio_recorder.drain_buffer()
        if audio is None or len(audio) == 0:
            return

        batch_elapsed = self._batch_window_start
        text = self._transcriber.transcribe_batch(
            audio,
            batch_elapsed,
            initial_prompt=self._build_initial_prompt(),
            emit_callback=False,
        )
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

    def _batch_transcribe(self) -> None:
        if self._audio_recorder is None or self._transcriber is None:
            return

        self._reap_batch_futures()
        if self._batch_futures:
            log.warning(
                "Skipping batch tick while previous batch "
                "transcription is still running"
            )
            self._set_status("Batch busy; carrying audio into the next window.")
            return

        audio = self._audio_recorder.drain_buffer()
        if audio is None or len(audio) == 0:
            return

        batch_elapsed = self._batch_window_start
        self._batch_window_start = self._elapsed
        self._submit_batch_transcription(audio, batch_elapsed)

    def _build_initial_prompt(self) -> str | None:
        parts = []
        if self._context_entries:
            parts.append(", ".join(self._context_entries))
        if self._previous_batch_tail:
            parts.append(self._previous_batch_tail)
        return ". ".join(parts) if parts else None

    def _update_tail(self, text: str) -> None:
        words = text.split()
        self._previous_batch_tail = " ".join(words[-35:])

    def _append_transcript(self, text: str, batch_elapsed: int | None = None) -> None:
        self._record_transcript(text, batch_elapsed)
        self._update_tail(text)

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

        self._bind_callbacks()
        self._ignore_batch_results = False
        self._session = Session(base_dir=config.DEFAULT_RECORDINGS_DIR)
        self._audio_recorder = AudioRecorder(
            output_path=self._session.audio_path,
            sample_rate=config.SAMPLE_RATE,
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
            BATCH_INTERVAL_SECONDS,
            self._on_batch_tick,
        )
        self._elapsed = 0
        self._batch_window_start = 0
        self._batch_countdown = BATCH_INTERVAL_SECONDS
        self._word_count = 0
        self.state = AppState.RECORDING
        self._tick_timer.resume()
        self._set_status("Listening…")

        # Show session header in transcript pane
        with contextlib.suppress(NoMatches):
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.query_one("#captions", RichLog).write(
                f"[dim]Session Start: {ts}[/dim]"
            )

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
            self._write_pause_marker()
            return

        if self.state is AppState.PAUSED:
            self.state = AppState.RECORDING
            if self._audio_recorder is not None:
                self._audio_recorder.resume()
            self._batch_countdown = BATCH_INTERVAL_SECONDS
            self._set_status("Listening…")
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
            has_active_transcriber = self._transcriber is not None and (
                self._transcriber.is_ready or self._transcriber.has_active_worker
            )
            needs_cleanup = has_open_resources or has_active_transcriber
            if not needs_cleanup:
                return

            if hasattr(self, "_tick_timer"):
                self._tick_timer.pause()
            if hasattr(self, "_batch_timer"):
                self._batch_timer.pause()

            if self._audio_recorder is not None:
                try:
                    self._audio_recorder.stop()
                except Exception as exc:
                    log.exception("Failed to stop audio recorder")
                    if include_ui:
                        self._show_error(f"Could not stop audio recorder: {exc}")

            try:
                batch_workers_finished, captured = self._wait_for_batch_workers()
                self._ignore_batch_results = True
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
            except Exception as exc:
                log.exception(
                    "Failed while flushing batch transcription during shutdown"
                )
                if include_ui:
                    self._show_error(f"Could not flush final transcript batch: {exc}")
            finally:
                self._audio_recorder = None

            if self._batch_executor is not None:
                self._batch_executor.shutdown(wait=False, cancel_futures=False)
                self._batch_executor = None

            if self._transcriber is not None and (
                self._transcriber.is_ready or self._transcriber.has_active_worker
            ):
                try:
                    self._transcriber.shutdown(timeout=5)
                except Exception as exc:
                    log.exception("Failed to shut down transcriber")
                    if include_ui:
                        self._show_error(f"Could not shut down transcriber: {exc}")

            if self._session is not None:
                try:
                    self._session.write_end_header()
                    self._session.finalize()
                except Exception as exc:
                    log.exception("Failed to finalize session")
                    if include_ui:
                        self._show_error(f"Could not finalize session: {exc}")
                finally:
                    self._session = None

            try:
                self.state = AppState.IDLE
            except Exception:
                self._reactive_state = AppState.IDLE

    _NOTE_PREFIXES: ClassVar[dict[str, str]] = {
        "/task": "TASK",
        "/t": "TASK",
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

        # Parse optional prefix: /action, /followup, /a, /f
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

        timestamp = datetime.now().strftime("%H:%M:%S")
        file_line = f"[{tag}] {timestamp} -- {raw}"
        styled_line = (
            f"[bold cyan][{tag}][/bold cyan] [dim]{timestamp}[/dim] \u2014 {raw}"
        )

        try:
            self.query_one("#captions", RichLog).write(styled_line)
        except NoMatches:
            log.error("Note pane unavailable: %s", file_line)

        if self._session is not None:
            self._session.append_sentence(file_line)

        self._note_counts[tag] = self._note_counts.get(tag, 0) + 1
        self._word_count += len(raw.split())
        self._sync_info_bar()
        self._update_context_display()
        input_widget.value = ""

    def _handle_context_start(self, raw: str) -> None:
        text = raw.strip()
        self._awaiting_context = False

        # Restore notes label
        with contextlib.suppress(NoMatches):
            label = self.query_one("#notes-label", Static)
            label.update(
                "Notes  [dim](/task  /context  /clear · Enter to submit)[/dim]"
            )

        # Clear the input
        with contextlib.suppress(NoMatches):
            self.query_one("#note-input", Input).value = ""

        # Start recording
        if not self._preflight_check():
            return
        self._start_recording()

        # Write context if provided
        if text:
            self._context_entries.append(text)
            self._note_counts["CONTEXT"] += 1
            timestamp = datetime.now().strftime("%H:%M:%S")
            file_line = f"[CONTEXT] {timestamp} -- {text}"
            styled_line = (
                f"[bold magenta][CONTEXT][/bold magenta]"
                f" [dim]{timestamp}[/dim] \u2014 {text}"
            )
            with contextlib.suppress(NoMatches):
                self.query_one("#captions", RichLog).write(styled_line)
            if self._session is not None:
                self._session.append_sentence(file_line)
            self._update_context_display()

    def _handle_add_context(self, raw: str) -> None:
        # Strip the /context prefix
        for prefix in ("/context ", "/context"):
            if raw.lower().startswith(prefix):
                text = raw[len(prefix) :].strip()
                break
        else:
            text = ""

        if not text:
            with contextlib.suppress(NoMatches):
                self.query_one("#note-input", Input).value = ""
            return

        self._context_entries.append(text)
        self._note_counts["CONTEXT"] += 1
        timestamp = datetime.now().strftime("%H:%M:%S")
        file_line = f"[CONTEXT] {timestamp} -- {text}"
        styled_line = (
            f"[bold magenta][CONTEXT][/bold magenta]"
            f" [dim]{timestamp}[/dim] \u2014 {text}"
        )
        with contextlib.suppress(NoMatches):
            self.query_one("#captions", RichLog).write(styled_line)
        if self._session is not None:
            self._session.append_sentence(file_line)
        self._update_context_display()
        with contextlib.suppress(NoMatches):
            self.query_one("#note-input", Input).value = ""

    def _handle_clear_context(self) -> None:
        self._context_entries.clear()
        self._note_counts["CONTEXT"] = 0
        self._previous_batch_tail = ""
        self._update_context_display()
        self._set_status("Context cleared")
        with contextlib.suppress(NoMatches):
            self.query_one("#note-input", Input).value = ""

    def _update_context_display(self) -> None:
        try:
            display = self.query_one("#context-display", Static)
        except NoMatches:
            return
        parts = []
        for label, key in [
            ("Context", "CONTEXT"),
            ("Tasks", "TASK"),
            ("Notes", "NOTE"),
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
        if self._awaiting_context:
            self._handle_context_start(event.input.value)
            return
        raw = event.input.value.strip()
        lower = raw.lower()
        if lower in ("/help", "/h", "?"):
            self._show_help()
            return
        if lower == "/clear":
            self._handle_clear_context()
            return
        if lower.startswith("/context ") or lower == "/context":
            self._handle_add_context(raw)
            return
        self._submit_note()

    def on_unmount(self) -> None:
        if self._batch_executor is not None:
            self._batch_executor.shutdown(wait=True, cancel_futures=False)
            self._batch_executor = None
