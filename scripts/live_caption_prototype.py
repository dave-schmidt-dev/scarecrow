"""Live captions prototype — standalone Textual app.

Tests speculative Parakeet captions in isolation from the main Scarecrow app.
Two-tier transcription: speculative (full buffer peek) + committed (VAD drain).

Usage:
    uv run python scripts/live_caption_prototype.py
"""

from __future__ import annotations

import contextlib
import functools
import logging
import tempfile
import time
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import ClassVar

import numpy as np
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.css.query import NoMatches
from textual.widgets import Footer, RichLog, Static

from scarecrow.config import Config
from scarecrow.recorder import AudioRecorder
from scarecrow.runtime import ModelManager, configure_runtime_environment
from scarecrow.transcriber import Transcriber

log = logging.getLogger(__name__)

RICHLOG_MAX_LINES = 500


# ---------------------------------------------------------------------------
# Prototype-local helpers (not modifying scarecrow/ source)
# ---------------------------------------------------------------------------


def peek_buffer(
    recorder: AudioRecorder,
) -> tuple[np.ndarray, list[float]] | None:
    """Non-destructive read of the full recorder audio buffer.

    Returns (audio_float32_16kHz, chunk_energies) for all accumulated
    audio since the last drain. The buffer is not modified.
    """
    with recorder._buffer_lock:
        if not recorder._audio_chunks:
            return None
        chunks = [c.copy() for c in recorder._audio_chunks]
        energies = list(recorder._chunk_energies)
    return recorder._finalize_audio(chunks), energies


def has_enough_speech(energies: list[float], cfg: Config) -> bool:
    """Speech-ratio gate — mirrors main app logic (app.py:1128-1143)."""
    if not energies:
        return False
    floor = cfg.VAD_SILENCE_THRESHOLD * 0.5
    speech = sum(1 for e in energies if e >= floor)
    ratio = speech / len(energies)
    if ratio >= cfg.VAD_MIN_SPEECH_RATIO:
        return True
    # Secondary low-floor check for quiet sources
    low_floor = cfg.VAD_SILENCE_THRESHOLD * 0.15
    low_ratio = sum(1 for e in energies if e >= low_floor) / len(energies)
    return low_ratio >= cfg.VAD_MIN_SPEECH_RATIO * 2


# ---------------------------------------------------------------------------
# Textual app
# ---------------------------------------------------------------------------


class LiveCaptionApp(App[None]):
    """Prototype: speculative + committed live captions via Parakeet."""

    TITLE = "Live Captions (Prototype)"
    ENABLE_COMMAND_PALETTE = False

    CSS = """
    Screen {
        layout: vertical;
        background: $surface;
    }
    #status-bar {
        height: 1;
        dock: top;
        padding: 0 1;
        background: $panel;
    }
    .pane-label {
        height: 1;
        margin: 0 1;
        padding: 0 1;
        color: $text-muted;
        text-style: bold;
    }
    #captions {
        border: solid $primary-darken-2;
        height: 1fr;
        min-height: 3;
        margin: 0 1;
        padding: 0 1;
        scrollbar-gutter: stable;
        overflow-x: hidden;
    }
    #live-pane {
        height: auto;
        max-height: 6;
        margin: 0 1;
        padding: 0 1;
        color: $text-muted;
    }
    """

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("q", "quit", "Quit", show=True),
        Binding("ctrl+q", "quit", "Quit", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._cfg = Config()
        self._model_manager = ModelManager(cfg=self._cfg)
        self._transcriber = Transcriber(
            model_manager=self._model_manager, cfg=self._cfg
        )
        self._recorder: AudioRecorder | None = None
        self._executor: ThreadPoolExecutor | None = None

        # Future tracking
        self._committed_future: Future[str | None] | None = None
        self._speculative_future: Future[str | None] | None = None
        self._commit_generation: int = 0

        # State
        self._elapsed: int = 0
        self._word_count: int = 0
        self._recording_start: float | None = None
        self._ignore_results: bool = False

        # Paragraph accumulator (same pattern as main app)
        self._current_paragraph: str = ""
        self._paragraph_line_count: int = 0

        # Temp file for WAV output (AudioRecorder requires a path)
        self._tmp_wav: Path | None = None

    def compose(self) -> ComposeResult:
        yield Static("", id="status-bar")
        model_label = self._cfg.PARAKEET_MODEL.split("/")[-1]
        yield Static(
            f"Transcript  [dim]({model_label} · VAD + speculative)[/dim]",
            classes="pane-label",
        )
        yield RichLog(
            id="captions",
            highlight=True,
            markup=True,
            wrap=True,
            min_width=0,
            max_lines=RICHLOG_MAX_LINES,
        )
        yield Static(
            "Live preview  [dim](speculative — replaced on commit)[/dim]",
            classes="pane-label",
        )
        yield Static("", id="live-pane")
        yield Footer()

    def on_mount(self) -> None:
        self._sync_status("Initializing...")
        # Prepare runtime
        configure_runtime_environment()
        self._transcriber.prepare()

        # Preload model (blocks UI briefly but prevents first-tick stall)
        self._sync_status("Loading Parakeet model...")
        self._transcriber.preload_batch_model()

        # Create executor (single thread for Metal safety)
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="caption-transcribe"
        )

        # Preflight: check for audio input
        try:
            import sounddevice as sd

            devices = sd.query_devices()
            try:
                has_input = any(d.get("max_input_channels", 0) > 0 for d in devices)
            except TypeError:
                has_input = devices.get("max_input_channels", 0) > 0
            if not has_input:
                self._sync_status("[bold red]No audio input devices found[/bold red]")
                return
        except Exception as exc:
            self._sync_status(f"[bold red]Audio error: {exc}[/bold red]")
            return

        # Start recording to temp file
        self._tmp_wav = Path(tempfile.mktemp(suffix=".wav", prefix="livecap_"))
        self._recorder = AudioRecorder(
            self._tmp_wav,
            sample_rate=self._cfg.RECORDING_SAMPLE_RATE,
            cfg=self._cfg,
        )
        self._recorder.start()
        self._recording_start = time.monotonic()

        # Start timers
        self.set_interval(1, self._tick_elapsed)
        self.set_interval(self._cfg.VAD_POLL_INTERVAL_MS / 1000.0, self._on_vad_poll)
        self.set_interval(0.5, self._on_speculative_tick)

        self._sync_status("[bold green]REC[/bold green]  0:00:00  0 words")

    # ── Status bar ──

    def _sync_status(self, text: str = "") -> None:
        if text:
            self._status_text = text
        else:
            h = self._elapsed // 3600
            m = (self._elapsed % 3600) // 60
            s = self._elapsed % 60
            mic_level = self._recorder.peak_level if self._recorder else 0.0
            bars = int(mic_level * 10)
            meter = "█" * bars + "░" * (10 - bars)
            self._status_text = (
                f"[bold green]REC[/bold green]  "
                f"{h}:{m:02d}:{s:02d}  "
                f"{self._word_count} words  "
                f"[dim]{meter}[/dim]"
            )
        with contextlib.suppress(NoMatches):
            self.query_one("#status-bar", Static).update(self._status_text)

    def _tick_elapsed(self) -> None:
        if self._recording_start is not None:
            self._elapsed = int(time.monotonic() - self._recording_start)
        self._sync_status()

    # ── Executor helpers ──

    def _ensure_executor(self) -> ThreadPoolExecutor:
        if self._executor is None:
            self._executor = ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="caption-transcribe"
            )
        return self._executor

    # ── VAD poll (committed path) ──

    def _on_vad_poll(self) -> None:
        """Poll for silence boundaries → committed transcription."""
        if self._recorder is None or self._ignore_results:
            return
        if self._committed_future and not self._committed_future.done():
            return

        result = self._recorder.drain_to_silence()
        if result is None:
            return
        audio, energies = result
        if len(audio) == 0:
            return
        if not has_enough_speech(energies, self._cfg):
            return

        self._commit_generation += 1  # invalidate in-flight speculative
        batch_elapsed = self._elapsed
        fn = functools.partial(
            self._transcriber.transcribe_batch,
            emit_callback=False,
        )
        self._committed_future = self._ensure_executor().submit(
            fn, audio, batch_elapsed
        )
        self._committed_future.add_done_callback(self._on_committed_done)

    def _on_committed_done(self, future: Future[str | None]) -> None:
        """Handle committed transcription result (runs on executor thread)."""
        if self._ignore_results:
            return
        try:
            text = future.result()
            if text:
                self.call_from_thread(self._commit_transcript, text)
        except Exception:
            log.exception("Committed transcription failed")

    def _commit_transcript(self, text: str) -> None:
        """Append committed text to RichLog and clear live pane."""
        self._word_count += len(text.split())

        try:
            captions = self.query_one("#captions", RichLog)
        except NoMatches:
            return

        # Paragraph accumulation (same pattern as main app)
        if self._current_paragraph:
            self._current_paragraph += " " + text
        else:
            self._current_paragraph = text

        if self._paragraph_line_count > 0:
            del captions.lines[-self._paragraph_line_count :]
            captions._line_cache.clear()
        before = len(captions.lines)
        captions.write(self._current_paragraph)
        self._paragraph_line_count = len(captions.lines) - before

        # Clear speculative live pane
        self._update_live_pane("")

        self._sync_status()

    # ── Speculative path ──

    def _on_speculative_tick(self) -> None:
        """Peek buffer → speculative inference if executor is idle."""
        if self._recorder is None or self._ignore_results:
            return
        # Gate 1: committed is running → skip
        if self._committed_future and not self._committed_future.done():
            return
        # Gate 2: previous speculative still running → skip
        if self._speculative_future and not self._speculative_future.done():
            return

        result = peek_buffer(self._recorder)
        if result is None:
            return
        audio, energies = result
        if len(audio) == 0:
            return
        if not has_enough_speech(energies, self._cfg):
            return

        gen = self._commit_generation
        fn = functools.partial(
            self._transcriber.transcribe_batch,
            emit_callback=False,
            max_retries=0,  # no retries for throwaway text
        )
        self._speculative_future = self._ensure_executor().submit(fn, audio, 0)
        self._speculative_future.add_done_callback(
            functools.partial(self._on_speculative_done, gen)
        )

    def _on_speculative_done(self, gen: int, future: Future[str | None]) -> None:
        """Handle speculative result (runs on executor thread)."""
        if self._ignore_results:
            return
        try:
            if gen != self._commit_generation:
                return  # stale — committed fired since submission
            text = future.result()
            if text:
                self.call_from_thread(self._update_live_pane, text)
        except Exception:
            log.debug("Speculative inference failed", exc_info=True)

    def _update_live_pane(self, text: str) -> None:
        """Update the speculative preview pane."""
        try:
            pane = self.query_one("#live-pane", Static)
        except NoMatches:
            return
        if text:
            styled = Text(text, style="dim italic")
            pane.update(styled)
        else:
            pane.update("")

    # ── Shutdown ──

    def on_unmount(self) -> None:
        self._ignore_results = True

        if self._recorder is not None:
            try:
                self._recorder.stop()
            except Exception:
                log.warning("Error stopping recorder", exc_info=True)

        if self._executor is not None:
            self._executor.shutdown(wait=True, cancel_futures=True)
            self._executor = None

        self._model_manager.release_models()

        # Clean up temp WAV
        if self._tmp_wav and self._tmp_wav.exists():
            with contextlib.suppress(OSError):
                self._tmp_wav.unlink()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    app = LiveCaptionApp()
    app.run()


if __name__ == "__main__":
    main()
