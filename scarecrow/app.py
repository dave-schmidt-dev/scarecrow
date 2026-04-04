"""Textual TUI application for Scarecrow."""

from __future__ import annotations

import contextlib
import functools
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
from textual.events import Click
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.widgets import Footer, Input, OptionList, RichLog, Static
from textual.widgets.option_list import Option

from scarecrow import config
from scarecrow.config import Config
from scarecrow.echo_filter import EchoFilter
from scarecrow.recorder import AudioRecorder
from scarecrow.session import Session
from scarecrow.transcriber import TranscriberBindings

if TYPE_CHECKING:
    from scarecrow.sys_audio import SystemAudioCapture
    from scarecrow.transcriber import Transcriber

log = logging.getLogger(__name__)


BATCH_INTERVAL_SECONDS = config.BATCH_INTERVAL
RICHLOG_MAX_LINES = 500

_BT_KEYWORDS = (
    "airpod",
    "beats",
    "bose",
    "jabra",
    "sony",
    "jbl",
    "sennheiser",
    "plantronics",
    "poly",
    "anker",
    "nothing ear",
    "galaxy bud",
)


def _is_bluetooth_input() -> tuple[bool, str]:
    """Return (is_bt, device_name) for the current default input device.

    Uses system_profiler SPBluetoothDataType for reliable transport-type
    detection; falls back to keyword matching if that fails.
    """
    import json as _json
    import subprocess

    import sounddevice as sd

    try:
        dev_info = sd.query_devices(sd.default.device[0])
        dev_name: str = (
            dev_info.get("name", "") if isinstance(dev_info, dict) else str(dev_info)
        )
    except Exception:
        return False, ""

    # Primary: CoreAudio transport type via system_profiler
    try:
        result = subprocess.run(
            ["system_profiler", "SPBluetoothDataType", "-json"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        data = _json.loads(result.stdout)
        bt_section = data.get("SPBluetoothDataType", [{}])[0]
        # Devices appear under various keys; check all device_title entries
        bt_devices = bt_section.get("device_title", [])
        bt_names = [next(iter(d.keys())) for d in bt_devices if isinstance(d, dict)]
        for bt_name in bt_names:
            if dev_name.startswith(bt_name) or bt_name.startswith(dev_name):
                return True, dev_name
        # system_profiler succeeded but device not found in BT list
        return False, dev_name
    except Exception:
        pass

    # Fallback: keyword match on device name
    low = dev_name.lower()
    if any(kw in low for kw in _BT_KEYWORDS):
        return True, dev_name
    return False, dev_name


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
    sys_peak_level: reactive[float] = reactive(0.0, always_update=True)
    has_sys_audio: reactive[bool] = reactive(False)
    mic_muted: reactive[bool] = reactive(False)
    sys_muted: reactive[bool] = reactive(False)

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._mic_region: tuple[int, int] = (0, 0)
        self._sys_region: tuple[int, int] = (0, 0)

    def _render_meter(self, raw: float) -> tuple[str, str, str, str]:
        """Return (bar_char, color, meter_label, label_style) for a peak level."""
        import math

        bars = " ▁▂▃▄▅▆▇█"
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
        if scaled < 0.15:
            meter_label, label_style = "quiet", "dim"
        elif scaled < 0.4:
            meter_label, label_style = "low  ", "green"
        elif scaled < 0.75:
            meter_label, label_style = "norm ", "yellow"
        else:
            meter_label, label_style = "HIGH ", f"bold {color}"
        return bars[idx], color, meter_label, label_style

    def render(self) -> Text:
        label, style, icon = _STATE_STYLE[self.state]
        text = Text()
        text.append(f" {label} ", style=style)
        if icon:
            text.append(f" {icon}")
        width = self.size.width if self.size.width > 0 else 120
        if self.state is AppState.RECORDING:
            mic_start = text.cell_len
            text.append(" mic ", style="dim")
            if self.mic_muted:
                text.append("MUTED", style="dim")
            else:
                bar_char, color, meter_label, label_style = self._render_meter(
                    self.peak_level
                )
                text.append(bar_char, style=color)
                text.append(f" {meter_label}", style=label_style)
            self._mic_region = (mic_start, text.cell_len)
            if self.has_sys_audio and width >= 80:
                text.append("  │  ", style="dim")
                sys_start = text.cell_len
                text.append("sys ", style="dim")
                if self.sys_muted:
                    text.append("MUTED", style="dim")
                else:
                    sys_bar, sys_color, sys_meter_label, sys_label_style = (
                        self._render_meter(self.sys_peak_level)
                    )
                    text.append(sys_bar, style=sys_color)
                    text.append(f" {sys_meter_label}", style=sys_label_style)
                self._sys_region = (sys_start, text.cell_len)
            else:
                self._sys_region = (0, 0)
        text.append("  ")

        h = self.elapsed // 3600
        m = (self.elapsed % 3600) // 60
        s = self.elapsed % 60
        text.append(f"{h:02d}:{m:02d}:{s:02d}", style="bold")
        text.append("  ")

        # Drop word count and batch countdown on narrow terminals
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

    def on_click(self, event: Click) -> None:
        """Click on mic/sys level meters to toggle mute."""
        if event.button != 1:
            return
        x = event.x
        mic_s, mic_e = self._mic_region
        sys_s, sys_e = self._sys_region
        if mic_s <= x < mic_e:
            self.app.action_mute_mic()
        elif sys_s <= x < sys_e:
            self.app.action_mute_sys()


class ContextMenuScreen(ModalScreen[str | None]):
    """Menu for mute toggle and input gain presets."""

    DEFAULT_CSS = """
    ContextMenuScreen {
        align: center middle;
    }
    ContextMenuScreen OptionList {
        width: 34;
        height: auto;
        max-height: 26;
        background: $surface;
        border: solid $accent;
    }
    """

    def __init__(self, source: str | None = None, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._source = source  # None = combined menu (keybinding)
        self._dismissed = False

    def _build_source_options(self, source: str, prefix: str) -> list[Option | None]:
        app: ScarecrowApp = self.app  # type: ignore[assignment]
        is_muted = app._mic_muted if source == "mic" else app._sys_muted
        mute_label = "Unmute" if is_muted else "Mute"
        current = app._vad_sensitivity if source == "mic" else app._sys_vad_sensitivity
        label = "Mic" if source == "mic" else "Sys"
        opts: list[Option | None] = [
            Option(
                f"  {mute_label} {label}",
                id=f"{prefix}toggle_mute",
            ),
        ]
        for name in ("Low", "Normal", "High"):
            marker = "\u2022 " if name.lower() == current else "  "
            key = name.lower()
            opts.append(Option(f"{marker}{name} gain", id=f"{prefix}vad_{key}"))
        return opts

    def _build_input_device_options(self) -> list[Option | None]:
        """Build input device selection options, capped at 8 devices."""
        import sounddevice as sd

        app: ScarecrowApp = self.app  # type: ignore[assignment]
        # Prefer the explicitly tracked device ID (set after each switch).
        # Fall back to the recorder's opened device, then the system default.
        active_id: int | None = app._mic_device_id
        if active_id is None and app._audio_recorder is not None:
            active_id = app._audio_recorder._opened_device_id
        if active_id is None:
            with contextlib.suppress(Exception):
                active_id = sd.default.device[0]

        try:
            all_devices = sd.query_devices()
        except Exception:
            return []

        # Normalize to list (sounddevice may return a dict for single device)
        if isinstance(all_devices, dict):
            all_devices = [all_devices]

        sys_dev_id = app._sys_device_id  # BlackHole device — exclude
        input_devices: list[tuple[int, str]] = []
        for i, dev in enumerate(all_devices):
            if not isinstance(dev, dict):
                continue
            if dev.get("max_input_channels", 0) <= 0:
                continue
            if i == sys_dev_id:
                continue
            name: str = dev.get("name", f"Device {i}")
            if "blackhole" in name.lower():
                continue
            input_devices.append((i, name))
            if len(input_devices) >= 8:
                break

        opts: list[Option | None] = []
        for dev_id, name in input_devices:
            marker = "\u2022 " if dev_id == active_id else "  "
            opts.append(Option(f"{marker}{name}", id=f"input_device:{dev_id}"))
        return opts

    def compose(self) -> ComposeResult:
        if self._source is not None:
            options: list[Option | None] = self._build_source_options(self._source, "")
        else:
            input_opts = self._build_input_device_options()
            options = [
                Option("  \u2501 Mic \u2501", id="_header_mic"),
                *self._build_source_options("mic", "mic:"),
                None,  # separator
                Option("  \u2501 Sys \u2501", id="_header_sys"),
                *self._build_source_options("sys", "sys:"),
            ]
            if input_opts:
                options += [
                    None,  # separator
                    Option("  \u2501 Input \u2501", id="_header_input"),
                    *input_opts,
                ]
        yield OptionList(*options)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        opt_id = event.option.id or ""
        if opt_id.startswith("_header"):
            return  # ignore header clicks
        if not self._dismissed:
            self._dismissed = True
            self.dismiss(opt_id)

    def key_escape(self) -> None:
        if not self._dismissed:
            self._dismissed = True
            self.dismiss(None)

    def on_click(self, event: Click) -> None:
        if self._dismissed:
            return
        # Use screen coordinates for reliable hit-testing inside a
        # centered ModalScreen (event.x/y can differ from screen coords).
        if not self.query_one(OptionList).region.contains(
            event.screen_x, event.screen_y
        ):
            self._dismissed = True
            self.dismiss(None)


class ScarecrowApp(App[None]):
    """Always-recording TUI with realtime and batch transcription."""

    TITLE = "Scarecrow"
    CSS_PATH = "app.tcss"
    ENABLE_COMMAND_PALETTE = False

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("ctrl+p", "pause", "Pause/Resume", show=True),
        Binding("ctrl+v", "vad_menu", "Mute/Gain", show=True),
        Binding("ctrl+q", "quit", "Quit", show=True),
        Binding("ctrl+shift+q", "quick_quit", "Quick Quit", show=True),
        Binding("ctrl+shift+d", "discard_quit", "Discard", show=True),
    ]

    # VAD sensitivity presets: input gain multiplier
    _MIC_PRESETS: ClassVar[dict[str, float]] = {
        "low": 0.5,
        "normal": 1.0,
        "high": 2.0,
    }
    # Sys presets are lower — BlackHole digital loopback is near full-scale
    _SYS_PRESETS: ClassVar[dict[str, float]] = {
        "low": 0.125,
        "normal": 0.25,
        "high": 0.5,
    }

    state: reactive[AppState] = reactive(AppState.IDLE)
    _elapsed: reactive[int] = reactive(0)
    _word_count: reactive[int] = reactive(0)
    _batch_countdown: reactive[int] = reactive(BATCH_INTERVAL_SECONDS)

    def __init__(
        self,
        transcriber: Transcriber | None = None,
        *,
        cfg: Config | None = None,
        sys_audio: bool = True,
        mic_muted: bool = False,
        sys_muted: bool = False,
    ) -> None:
        super().__init__()
        self._cfg = cfg or config.config
        self._session: Session | None = None
        self._audio_recorder: AudioRecorder | None = None
        self._sys_audio_enabled = sys_audio
        self._sys_capture: SystemAudioCapture | None = None
        self._transcriber: Transcriber | None = transcriber
        self._status_message: str = ""
        self._status_is_error = False
        self._session_name: str = ""
        self._shutdown_summary = ""
        self._summary_path: Path | None = None
        self._batch_executor: ThreadPoolExecutor | None = None
        self._batch_futures: set[Future[str | None]] = set()
        self._mic_future: Future[str | None] | None = None
        self._sys_future: Future[str | None] | None = None
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
        # System audio paragraph accumulator (right-aligned, italic)
        self._sys_current_paragraph: str = ""
        self._sys_paragraph_line_count: int = 0
        self._last_paragraph_source: str = ""
        self._sys_batch_window_start: int = 0
        # Per-source mute state
        self._mic_muted: bool = mic_muted
        self._sys_muted: bool = sys_muted
        # Echo suppression — suppress mic transcripts that duplicate sys
        self._echo_filter = EchoFilter()
        # Holdoff: discard the first sys batch result after start/unmute
        # to avoid duplicate text while echo filter primes
        self._sys_holdoff: bool = True
        # Gain preset state
        self._vad_sensitivity: str = "normal"
        self._sys_vad_sensitivity: str = "normal"
        # Auto-segmentation state
        self._current_segment: int = 1
        self._rotation_pending: bool = False
        self._rotation_poll_count: int = 0
        self._sys_device_id: int | None = None
        self._mic_device_id: int | None = None  # tracks active mic device for menu
        # Quit-flow state
        self._skip_summary: bool = False
        self._discard_mode: bool = False
        self._completed_session: Session | None = None
        self._awaiting_discard_confirm: bool = False

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
        "     Scarecrow v1.5[/dim]"
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

        # Bluetooth input warning (non-blocking — never prevents startup)
        is_bt, bt_name = _is_bluetooth_input()
        if is_bt:
            log.warning("Bluetooth input detected: %s", bt_name)
            self.notify(
                f"Bluetooth input: {bt_name}"
                " — consider switching to built-in mic (Ctrl+V)",
                severity="warning",
                timeout=8,
            )

        return True

    _DEVICE_LOSS_THRESHOLD: ClassVar[float] = 3.0  # seconds without callback

    def _tick(self) -> None:
        if self._recording_start_time is not None:
            self._elapsed = int(time.monotonic() - self._recording_start_time)
        self._check_recorder_warnings()
        self._check_device_loss()
        if self.state is AppState.RECORDING:
            self._check_segment_boundary()
        self._sync_info_bar()

    def _check_segment_boundary(self) -> None:
        """Rotate audio files at segment boundaries."""
        if self.state is AppState.PAUSED:
            return
        if self._rotation_pending:
            return
        seg_duration = self._cfg.SEGMENT_DURATION_SECONDS
        expected = self._elapsed // seg_duration + 1
        if expected > self._current_segment:
            self._rotate_segment()

    def _rotate_segment(self) -> None:
        """Begin segment rotation — drain buffers and submit for transcription.

        Uses a non-blocking two-phase approach to avoid freezing the UI:
        1. Drain audio buffers and submit for transcription
        2. Poll until transcription is done, then finalize (swap recorders)
        """
        if self._session is None:
            return

        self._rotation_pending = True

        log.info(
            "Rotating segment %d → %d at elapsed=%ds",
            self._current_segment,
            self._current_segment + 1,
            self._elapsed,
        )

        # 1. Reap any already-completed futures to free submit slots
        self._reap_source_future("mic")
        self._reap_source_future("sys")
        self._reap_batch_futures()

        # 2. Hard-drain all buffered audio and submit for transcription
        #    (drain_buffer, not drain_to_silence — we want everything)
        if self._audio_recorder and not self._mic_muted:
            audio = self._audio_recorder.drain_buffer()
            if audio is not None and len(audio) > 0:
                batch_elapsed = self._batch_window_start
                self._batch_window_start = self._elapsed
                self._submit_batch_transcription(audio, batch_elapsed)
        if self._sys_capture and not self._sys_muted:
            sys_audio = self._sys_capture.drain_buffer()
            if sys_audio is not None and len(sys_audio) > 0:
                sys_elapsed = self._sys_batch_window_start
                self._sys_batch_window_start = self._elapsed
                self._submit_batch_transcription(sys_audio, sys_elapsed, source="sys")

        # 3. Poll for flush completion (non-blocking)
        self._rotation_poll_count = 0
        self.set_timer(0.1, self._poll_rotation_flush)

    _ROTATION_POLL_LIMIT: ClassVar[int] = 100  # 10s at 0.1s intervals

    def _poll_rotation_flush(self) -> None:
        """Check if segment rotation flush transcriptions are complete."""
        self._rotation_poll_count += 1
        mic_done = self._mic_future is None or self._mic_future.done()
        sys_done = self._sys_future is None or self._sys_future.done()
        if not (mic_done and sys_done):
            if self._rotation_poll_count < self._ROTATION_POLL_LIMIT:
                self.set_timer(0.1, self._poll_rotation_flush)
                return
            log.warning(
                "Rotation flush timed out after %ds",
                self._rotation_poll_count // 10,
            )
        # Reap the completed futures
        self._reap_batch_futures()
        # Schedule finalization after a short delay so that queued
        # call_from_thread callbacks (_record_transcript) are processed
        # by the event loop before we write the boundary event.
        self.set_timer(0.05, self._finalize_rotation)

    def _finalize_rotation(self) -> None:
        """Complete segment rotation: write boundary, swap recorders."""
        if self._session is None:
            self._rotation_pending = False
            return

        # 1. Stop recorders
        if self._audio_recorder:
            try:
                self._audio_recorder.stop()
            except Exception:
                log.exception("Failed to stop mic recorder during rotation")
        if self._sys_capture:
            try:
                self._sys_capture.stop()
            except Exception:
                log.exception("Failed to stop sys capture during rotation")

        # 2. Write boundary event (transcript flush has already landed)
        self._session.write_segment_boundary(self._current_segment, self._elapsed)

        # 3. Increment segment
        self._current_segment += 1

        # 4. Create and start new recorders
        new_mic_path = self._session.audio_path_for_segment(self._current_segment)
        self._audio_recorder = AudioRecorder(
            output_path=new_mic_path,
            sample_rate=self._cfg.RECORDING_SAMPLE_RATE,
            cfg=self._cfg,
        )
        try:
            self._audio_recorder.start()
        except Exception:
            log.exception("Failed to start new mic recorder after rotation")
            self._audio_recorder = None

        if self._sys_audio_enabled and self._sys_device_id is not None:
            from scarecrow.sys_audio import SystemAudioCapture

            new_sys_path = self._session.audio_sys_path_for_segment(
                self._current_segment
            )
            try:
                self._sys_capture = SystemAudioCapture(
                    output_path=new_sys_path,
                    device=self._sys_device_id,
                )
                self._sys_capture._gain = self._cfg.SYS_GAIN
                self._sys_capture.start()
            except Exception:
                log.exception("Failed to start new sys capture after rotation")
                self._sys_capture = None

        # 5. Re-apply mute state to new recorders
        if self._mic_muted and self._audio_recorder:
            self._audio_recorder.pause()
        if self._sys_muted and self._sys_capture:
            self._sys_capture.pause()

        # 6. Reset batch window starts, divider timer, and sys holdoff
        self._batch_window_start = self._elapsed
        self._sys_batch_window_start = self._elapsed
        self._last_divider_elapsed = self._elapsed - self._cfg.DIVIDER_INTERVAL
        self._sys_holdoff = True

        # 7. Write segment marker to UI
        with contextlib.suppress(NoMatches):
            rl = self.query_one("#captions", RichLog)
            rl.write(
                f"\n[bold]── segment {self._current_segment} "
                f"({self._elapsed // 60}m) ──[/bold]\n"
            )

        self._rotation_pending = False
        log.info("Segment rotation complete → segment %d", self._current_segment)

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
        bar.sys_peak_level = self._sys_capture.peak_level if self._sys_capture else 0.0
        has_sys = self._sys_capture is not None
        bar.has_sys_audio = has_sys
        bar.mic_muted = self._mic_muted
        bar.sys_muted = self._sys_muted

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
        # Don't restart when mic is intentionally muted — the stream is
        # stopped on purpose and stale-callback detection is irrelevant.
        if self._mic_muted:
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

        # Flush sys audio first so its text is in the echo filter before mic drains.
        # Route through the executor so all MLX inference stays on one OS thread.
        if self._sys_capture is not None:
            sys_audio = self._sys_capture.drain_buffer()
            if sys_audio is not None and len(sys_audio) > 0:
                sys_elapsed = self._sys_batch_window_start
                self._sys_batch_window_start = self._elapsed
                try:
                    fn = functools.partial(
                        self._transcriber.transcribe_batch,
                        source="sys",
                        emit_callback=False,
                        max_retries=0,
                    )
                    text = (
                        self._ensure_batch_executor()
                        .submit(fn, sys_audio, sys_elapsed)
                        .result(timeout=30)
                    )
                except Exception:
                    log.exception("Flush: sys audio transcription failed")
                    text = None
                if text:
                    self._record_transcript(text, sys_elapsed, source="sys")

        # Then flush mic async
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
            "  Ctrl+M / click mic  Mute / unmute mic\n"
            "  Ctrl+Shift+S / click sys  Mute / unmute sys audio\n"
            "  Ctrl+Shift+Q        Quick Quit (skip summary)\n"
            "  Ctrl+Q              Quit\n"
            "  Ctrl+Shift+D        Discard session & quit\n"
            "  Enter               Submit note\n"
            "\n"
            "[bold]Launch flags:[/bold]\n"
            "  --no-sys-audio      Disable system audio capture\n"
            "  --mic-only          Start with sys audio muted\n"
            "  --sys-only          Start with mic muted"
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
                    on_sys_batch_result=self._on_sys_batch_result,
                    on_error=self._on_transcriber_error,
                )
            )

    def _on_batch_result(self, text: str, batch_elapsed: int) -> None:
        if self._ignore_batch_results:
            log.debug("Ignoring late batch result during shutdown")
            return
        if self._echo_filter.is_echo(text):
            return
        self._echo_filter.record_mic(text)
        self._post_to_ui(
            functools.partial(self._record_transcript, source="mic"),
            text,
            batch_elapsed,
        )

    def _on_sys_batch_result(self, text: str, batch_elapsed: int) -> None:
        if self._ignore_batch_results:
            return
        self._echo_filter.record_sys(text)
        # Discard first sys result after start/unmute to let echo filter prime
        if self._sys_holdoff:
            self._sys_holdoff = False
            log.debug("Sys holdoff: discarding first batch result")
            return
        if self._echo_filter.is_sys_echo(text):
            return
        self._post_to_ui(
            functools.partial(self._record_transcript, source="sys"),
            text,
            batch_elapsed,
        )

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
            # Single worker ensures all MLX/Metal inference runs on the same OS
            # thread. The Metal backend has thread-local state; calling generate()
            # from multiple threads — even serially with a lock — causes a SIGSEGV.
            self._batch_executor = ThreadPoolExecutor(
                max_workers=1,
                thread_name_prefix="batch-transcribe",
            )
        return self._batch_executor

    def _reap_source_future(self, source: str) -> bool:
        """Reap the per-source future. Returns True if the slot is free."""
        future = self._mic_future if source == "mic" else self._sys_future
        if future is None:
            return True
        if not future.done():
            return False
        # Reap it
        try:
            future.result(timeout=0)
        except Exception as exc:
            log.exception("Batch worker failed (%s)", source)
            self._warn_transcript(f"Batch transcription failed: {exc}")
        if source == "mic":
            self._mic_future = None
        else:
            self._sys_future = None
        # Also remove from the shared set used by shutdown
        self._batch_futures.discard(future)
        return True

    def _submit_batch_transcription(
        self, audio, batch_elapsed: int, *, source: str = "mic"
    ) -> bool:
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

        # Per-source busy check — mic and sys can run concurrently
        if not self._reap_source_future(source):
            log.warning(
                "Skipping %s batch tick while previous %s "
                "transcription is still running",
                source,
                source,
            )
            return False

        fn = functools.partial(self._transcriber.transcribe_batch, source=source)
        future = self._ensure_batch_executor().submit(fn, audio, batch_elapsed)
        if source == "mic":
            self._mic_future = future
        else:
            self._sys_future = future
        # Keep in shared set for shutdown drain
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
            self._sys_current_paragraph = ""
            self._sys_paragraph_line_count = 0
            self._last_paragraph_source = ""

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
        source: str = "mic",
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
                    # Freeze both paragraphs, write divider, start fresh
                    self._current_paragraph = ""
                    self._paragraph_line_count = 0
                    self._sys_current_paragraph = ""
                    self._sys_paragraph_line_count = 0
                    self._last_paragraph_source = ""
                    captions.write(f"[dim]{divider}[/dim]")

                # When switching sources, freeze the other paragraph —
                # reset both line count AND accumulated text so the old
                # paragraph stays in place and the new source starts fresh.
                if source != self._last_paragraph_source:
                    if self._last_paragraph_source == "mic":
                        self._current_paragraph = ""
                        self._paragraph_line_count = 0
                    elif self._last_paragraph_source == "sys":
                        self._sys_current_paragraph = ""
                        self._sys_paragraph_line_count = 0

                if source == "sys":
                    # Dim-styled sys audio with subtle prefix.
                    # No paragraph accumulation — each drain is independent
                    # speech, unlike mic where consecutive batches continue
                    # the same utterance.
                    text_obj = Text()
                    text_obj.append("◁ ", style="dim cyan")
                    text_obj.append(escape(text), style="dim")
                    captions.write(text_obj)
                else:
                    # Left-aligned for mic (default)
                    if self._current_paragraph:
                        self._current_paragraph += " " + escape(text)
                    else:
                        self._current_paragraph = escape(text)

                    if self._paragraph_line_count > 0:
                        del captions.lines[-self._paragraph_line_count :]
                        captions._line_cache.clear()
                    before = len(captions.lines)
                    captions.write(self._current_paragraph)
                    self._paragraph_line_count = len(captions.lines) - before

                self._last_paragraph_source = source

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
                    "source": source,
                }
            )

        self._word_count += len(text.split())
        if include_ui:
            self._set_status("")
            self._sync_info_bar()
            self._prune_richlog()

    def _flush_final_batch(self, *, include_ui: bool = True) -> None:
        """Drain and transcribe remaining audio via the executor.

        All calls go through the single-worker executor so MLX Metal inference
        always runs on the same OS thread. Calling transcribe_batch() directly
        on the main thread causes a Metal thread-local-state SIGSEGV.
        """
        if self._transcriber is None:
            return

        executor = self._ensure_batch_executor()

        # Flush mic
        if self._audio_recorder is not None:
            audio = self._audio_recorder.drain_buffer()
            if audio is not None and len(audio) > 0:
                batch_elapsed = self._batch_window_start
                try:
                    fn = functools.partial(
                        self._transcriber.transcribe_batch,
                        emit_callback=False,
                        max_retries=0,
                    )
                    text = executor.submit(fn, audio, batch_elapsed).result(timeout=30)
                except Exception as exc:
                    log.exception("Final mic batch transcription failed")
                    if include_ui:
                        self._show_error(f"Final batch transcription failed: {exc}")
                    text = None
                if text:
                    self._record_transcript(text, batch_elapsed, include_ui=include_ui)

        # Flush sys audio
        if self._sys_capture is not None:
            sys_audio = self._sys_capture.drain_buffer()
            if sys_audio is not None and len(sys_audio) > 0:
                sys_elapsed = self._sys_batch_window_start
                try:
                    fn = functools.partial(
                        self._transcriber.transcribe_batch,
                        source="sys",
                        emit_callback=False,
                        max_retries=0,
                    )
                    text = executor.submit(fn, sys_audio, sys_elapsed).result(
                        timeout=30
                    )
                except Exception:
                    log.exception("Final sys batch transcription failed")
                    text = None
                if text:
                    self._record_transcript(
                        text, sys_elapsed, include_ui=include_ui, source="sys"
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
        if timed_out:
            self._ignore_batch_results = True
            # Do NOT shutdown/recreate the executor. shutdown(wait=False) leaves
            # the old thread alive doing MLX Metal work; the next
            # _ensure_batch_executor() creates a NEW thread, and two threads
            # hitting the Metal Device singleton concurrently causes SIGSEGV.
            # We simply abandon the timed-out future and keep the executor alive.
        return not timed_out, captured

    def _vad_transcribe(self) -> None:
        """Drain audio at silence boundaries (parakeet backend)."""
        if self._audio_recorder is None or self._transcriber is None:
            return

        if not self._reap_source_future("mic"):
            return

        result = self._audio_recorder.drain_to_silence()
        if result is None:
            return
        audio, chunk_energies = result
        if len(audio) == 0:
            return

        # Speech-frame-ratio gate: skip if too few chunks have speech.
        # Uses half the silence threshold to avoid filtering quiet-but-real
        # speech (peak at "norm" level ≈ RMS near the silence threshold).
        # Secondary low-floor check handles reduced-level audio environments
        # (e.g. Mac phone calls where mic RMS is well below VAD threshold).
        # The low-floor path requires a HIGHER speech ratio (2x) because
        # weak signals need more evidence of real speech to avoid sending
        # near-silence to Parakeet (which hallucinates on it).
        if chunk_energies:
            energy_floor = self._cfg.VAD_SILENCE_THRESHOLD * 0.5
            speech_chunks = sum(1 for e in chunk_energies if e >= energy_floor)
            speech_ratio = speech_chunks / len(chunk_energies)
            if speech_ratio < self._cfg.VAD_MIN_SPEECH_RATIO:
                low_floor = self._cfg.VAD_SILENCE_THRESHOLD * 0.15
                low_speech_ratio = self._cfg.VAD_MIN_SPEECH_RATIO * 2
                low_chunks = sum(1 for e in chunk_energies if e >= low_floor)
                low_ratio = low_chunks / len(chunk_energies)
                if low_ratio < low_speech_ratio:
                    log.debug(
                        "Skipping low-speech buffer (%.0f%% < %.0f%%)",
                        speech_ratio * 100,
                        self._cfg.VAD_MIN_SPEECH_RATIO * 100,
                    )
                    return
                log.debug(
                    "Low-signal audio (primary=%.0f%%, low=%.0f%%) — proceeding",
                    speech_ratio * 100,
                    low_ratio * 100,
                )

        batch_elapsed = self._batch_window_start
        self._batch_window_start = self._elapsed
        self._submit_batch_transcription(audio, batch_elapsed)

    def _sys_vad_transcribe(self) -> None:
        """Drain system audio at silence boundaries."""
        if self._sys_capture is None or self._transcriber is None:
            return

        if not self._reap_source_future("sys"):
            return

        result = self._sys_capture.drain_to_silence(
            silence_threshold=self._cfg.SYS_VAD_SILENCE_THRESHOLD,
            min_silence_ms=self._cfg.SYS_VAD_MIN_SILENCE_MS,
            max_buffer_seconds=self._cfg.VAD_MAX_BUFFER_SECONDS,
            min_buffer_seconds=5.0,
        )
        if result is None:
            return
        audio, chunk_energies = result
        if len(audio) == 0:
            return

        if chunk_energies:
            energy_floor = self._cfg.SYS_VAD_SILENCE_THRESHOLD * 0.5
            speech_chunks = sum(1 for e in chunk_energies if e >= energy_floor)
            speech_ratio = speech_chunks / len(chunk_energies)
            if speech_ratio < self._cfg.SYS_VAD_MIN_SPEECH_RATIO:
                max_e = max(chunk_energies) if chunk_energies else 0.0
                log.debug(
                    "Skipping low-speech sys buffer (%.0f%% < %.0f%%, "
                    "%d chunks, max_rms=%.6f, floor=%.6f)",
                    speech_ratio * 100,
                    self._cfg.SYS_VAD_MIN_SPEECH_RATIO * 100,
                    len(chunk_energies),
                    max_e,
                    energy_floor,
                )
                return

        batch_elapsed = self._sys_batch_window_start
        self._sys_batch_window_start = self._elapsed
        self._submit_batch_transcription(audio, batch_elapsed, source="sys")

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
        self._sys_current_paragraph = ""
        self._sys_paragraph_line_count = 0
        self._last_paragraph_source = ""
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

        if self._sys_audio_enabled:
            from scarecrow.sys_audio import SystemAudioCapture, find_blackhole_device

            dev = find_blackhole_device(self._cfg.SYSTEM_AUDIO_DEVICE)
            self._sys_device_id = dev
            if dev is not None:
                try:
                    self._sys_capture = SystemAudioCapture(
                        output_path=self._session.audio_sys_path,
                        device=dev,
                    )
                    self._sys_capture._gain = self._cfg.SYS_GAIN
                    self._sys_capture.start()
                except Exception:
                    log.warning("Failed to start system audio capture", exc_info=True)
                    self._sys_capture = None
            else:
                log.info(
                    "System audio device '%s' not found — mic only",
                    self._cfg.SYSTEM_AUDIO_DEVICE,
                )

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

        # Apply initial mute state from launch flags
        if self._mic_muted and self._audio_recorder is not None:
            self._audio_recorder.pause()
            self._write_mute_event("mic", True)
        if self._sys_muted and self._sys_capture is not None:
            self._sys_capture.pause()
            self._write_mute_event("sys", True)

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
        if self.state is AppState.RECORDING and not self._rotation_pending:
            # Sys drains first so its text is in the echo filter before
            # mic drains — mic bleed is then caught by is_echo().
            if self._sys_capture is not None and not self._sys_muted:
                self._sys_vad_transcribe()
            if not self._mic_muted:
                self._vad_transcribe()
        # Always update buffer age display (show whichever source has more)
        rec = self._audio_recorder
        mic_buf = float(rec.buffer_seconds) if rec else 0.0
        sys_buf = float(self._sys_capture.buffer_seconds) if self._sys_capture else 0.0
        self._batch_countdown = int(max(mic_buf, sys_buf))
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
                if self._sys_capture is not None:
                    # Flush sys audio buffer before pausing
                    self._reap_batch_futures()
                    if not self._batch_futures:
                        sys_audio = self._sys_capture.drain_buffer()
                        if sys_audio is not None and len(sys_audio) > 0:
                            sys_elapsed = self._sys_batch_window_start
                            self._sys_batch_window_start = self._elapsed
                            self._submit_batch_transcription(
                                sys_audio, sys_elapsed, source="sys"
                            )
                    else:
                        # Batch busy — discard to prevent stale replay
                        self._sys_capture.drain_buffer()
                    try:
                        self._sys_capture.pause()
                    except Exception:
                        log.warning("Failed to pause system audio", exc_info=True)
            self._set_status("Paused")
            self._write_pause_marker()
            return

        if self.state is AppState.PAUSED:
            self.state = AppState.RECORDING
            if self._audio_recorder is not None:
                # Only resume sources that aren't muted
                if not self._mic_muted:
                    try:
                        self._audio_recorder.resume()
                    except Exception as exc:
                        log.exception("Failed to resume audio recorder")
                        self._set_status(
                            f"Could not resume recorder: {exc}", error=True
                        )
                if self._sys_capture is not None and not self._sys_muted:
                    try:
                        self._sys_capture.resume()
                    except Exception:
                        log.warning("Failed to resume system audio", exc_info=True)
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

    def _write_mute_event(self, source: str, muted: bool) -> None:
        """Record a mute/unmute event in the session transcript."""
        if self._session is None:
            return
        self._session.append_event(
            {
                "type": "mute" if muted else "unmute",
                "source": source,
                "elapsed": self._elapsed,
                "timestamp": datetime.now().isoformat(timespec="seconds"),
            }
        )

    def _write_mute_status(self, source: str, muted: bool) -> None:
        """Write a mute/unmute status line to the RichLog."""
        action = "MUTED" if muted else "UNMUTED"
        label = "Mic" if source == "mic" else "Sys audio"
        timestamp = datetime.now().strftime("%H:%M:%S")
        styled = f"[dim]{timestamp}[/dim]  [bold yellow]{label} {action}[/bold yellow]"
        self._current_paragraph = ""
        self._paragraph_line_count = 0
        with contextlib.suppress(NoMatches):
            self.query_one("#captions", RichLog).write(styled)

    def _write_sensitivity_event(self, source: str, preset: str) -> None:
        """Record a sensitivity change in the transcript and session JSONL."""
        label = "Mic" if source == "mic" else "Sys audio"
        timestamp = datetime.now().strftime("%H:%M:%S")
        styled = (
            f"[dim]{timestamp}[/dim]  [bold cyan]{label} gain → {preset}[/bold cyan]"
        )
        self._current_paragraph = ""
        self._paragraph_line_count = 0
        with contextlib.suppress(NoMatches):
            self.query_one("#captions", RichLog).write(styled)
        if self._session is not None:
            self._session.append_event(
                {
                    "type": "sensitivity",
                    "source": source,
                    "preset": preset,
                    "elapsed": self._elapsed,
                    "timestamp": datetime.now().isoformat(timespec="seconds"),
                }
            )

    def action_mute_mic(self) -> None:
        if self.state is not AppState.RECORDING:
            return
        if self._audio_recorder is None:
            return
        self._mic_muted = not self._mic_muted
        log.info("Mic %s", "muted" if self._mic_muted else "unmuted")
        self._write_mute_event("mic", self._mic_muted)
        self._write_mute_status("mic", self._mic_muted)
        if self._mic_muted:
            # Drain buffer before muting — discard if executor busy
            self._reap_batch_futures()
            if not self._batch_futures:
                audio = self._audio_recorder.drain_buffer()
                if audio is not None and len(audio) > 0:
                    batch_elapsed = self._batch_window_start
                    self._batch_window_start = self._elapsed
                    self._submit_batch_transcription(audio, batch_elapsed)
            else:
                self._audio_recorder.drain_buffer()
            self._audio_recorder.pause()
        else:
            self._audio_recorder.resume()
        self._sync_info_bar()

    def action_mute_sys(self) -> None:
        if self.state is not AppState.RECORDING:
            return
        if self._sys_capture is None:
            return
        self._sys_muted = not self._sys_muted
        log.info("Sys %s", "muted" if self._sys_muted else "unmuted")
        self._write_mute_event("sys", self._sys_muted)
        self._write_mute_status("sys", self._sys_muted)
        if self._sys_muted:
            self._reap_batch_futures()
            if not self._batch_futures:
                sys_audio = self._sys_capture.drain_buffer()
                if sys_audio is not None and len(sys_audio) > 0:
                    sys_elapsed = self._sys_batch_window_start
                    self._sys_batch_window_start = self._elapsed
                    self._submit_batch_transcription(
                        sys_audio, sys_elapsed, source="sys"
                    )
            else:
                self._sys_capture.drain_buffer()
            self._sys_capture.pause()
        else:
            self._sys_holdoff = True  # prime echo filter before showing sys
            self._sys_capture.resume()
        self._sync_info_bar()

    def action_vad_menu(self) -> None:
        """Open the combined VAD sensitivity menu via keybinding."""
        self.push_context_menu(None)

    def push_context_menu(self, source: str | None) -> None:
        """Open the context menu. *source*=None shows both mic and sys."""
        if self.state is not AppState.RECORDING:
            return
        # Guard against stacking
        if any(isinstance(s, ContextMenuScreen) for s in self.screen_stack[1:]):
            return
        self.push_screen(
            ContextMenuScreen(source),
            callback=self._handle_context_menu,
        )

    def _handle_context_menu(self, result: str | None) -> None:
        """Apply the user's context menu selection."""
        if not result:
            return

        # Input device switch ("input_device:7")
        if result.startswith("input_device:"):
            try:
                dev_id = int(result.split(":", 1)[1])
            except (ValueError, IndexError):
                return
            self._switch_input_device(dev_id)
            return

        # Parse prefixed IDs from combined menu ("mic:vad_low")
        if ":" in result:
            source, action = result.split(":", 1)
        else:
            source, action = "mic", result

        if action == "toggle_mute":
            if source == "mic":
                self.action_mute_mic()
            else:
                self.action_mute_sys()
            return
        # Input gain presets
        preset = action.removeprefix("vad_")
        presets = self._SYS_PRESETS if source == "sys" else self._MIC_PRESETS
        if preset not in presets:
            return
        gain = presets[preset]
        if source == "mic":
            self._vad_sensitivity = preset
            self._cfg.MIC_GAIN = gain
            log.info("Mic gain → %s (%.1fx)", preset, gain)
        else:
            self._sys_vad_sensitivity = preset
            self._cfg.SYS_GAIN = gain
            if self._sys_capture:
                self._sys_capture._gain = gain
            log.info("Sys gain → %s (%.1fx)", preset, gain)
        self._write_sensitivity_event(source, preset)
        self._set_status(f"{source.upper()} gain: {preset}")

    def _switch_input_device(self, device_id: int) -> None:
        """Switch the mic recorder to a different input device.

        Reuses the segment rotation machinery: drains buffers, waits for
        in-flight futures, stops the current recorder, then starts a new one
        on the next segment audio path (to avoid overwriting existing audio).
        """
        import sounddevice as sd

        if self._session is None or self._audio_recorder is None:
            return

        # No-op if already recording from this device
        if self._audio_recorder._opened_device_id == device_id:
            return

        try:
            dev_name: str = sd.query_devices(device_id).get(
                "name", f"Device {device_id}"
            )
        except Exception:
            dev_name = f"Device {device_id}"

        log.info("Switching input device → %d (%s)", device_id, dev_name)

        # 1. Drain VAD buffer
        if not self._mic_muted:
            self._audio_recorder.drain_to_silence()

        # 2. Wait for in-flight batch futures
        self._reap_batch_futures()
        for fut in list(self._batch_futures):
            try:
                fut.result(timeout=5)
            except Exception:
                log.warning("In-flight batch timed out during device switch")
        self._batch_futures.clear()

        # 3. Stop current mic recorder
        try:
            self._audio_recorder.stop()
        except Exception:
            log.exception("Failed to stop mic recorder during device switch")

        # 4. Write device-change event to transcript
        self._session.append_event(
            {
                "type": "input_device_changed",
                "device_id": device_id,
                "device_name": dev_name,
                "elapsed": self._elapsed,
                "timestamp": datetime.now().isoformat(timespec="seconds"),
            }
        )

        # 5. Bump segment so the new recorder writes to a fresh audio file
        #    (avoids overwriting audio already written by the previous recorder)
        self._current_segment += 1
        new_mic_path = self._session.audio_path_for_segment(self._current_segment)

        # 6. Start new recorder with explicit device
        self._audio_recorder = AudioRecorder(
            output_path=new_mic_path,
            sample_rate=self._cfg.RECORDING_SAMPLE_RATE,
            device=device_id,
            cfg=self._cfg,
        )
        try:
            self._audio_recorder.start()
        except Exception:
            log.exception("Failed to start recorder on new device")
            self._audio_recorder = None
            self._set_status(f"Failed to switch to {dev_name}", error=True)
            return

        # 7. Re-apply mute state
        if self._mic_muted:
            self._audio_recorder.pause()

        # 8. Track active device for Ctrl+V menu indicator
        self._mic_device_id = device_id

        # 9. Reset batch window
        self._batch_window_start = self._elapsed

        self.notify(f"Input switched: {dev_name}", timeout=4)
        log.info("Input device switch complete → %s", dev_name)

    def action_quit(self) -> None:
        log.info("action_quit triggered (ctrl+q)")
        self._shutdown_summary = self._collect_shutdown_metrics()
        self._set_status("Shutting down…")
        self.set_timer(0.3, self._deferred_quit)

    def action_quick_quit(self) -> None:
        log.info("action_quick_quit triggered (ctrl+shift+q)")
        self._skip_summary = True
        self._shutdown_summary = self._collect_shutdown_metrics()
        self._set_status("Shutting down (no summary)…")
        self.set_timer(0.3, self._deferred_quit)

    def action_discard_quit(self) -> None:
        if not self._awaiting_discard_confirm:
            self._awaiting_discard_confirm = True
            self._set_status(
                "Discard session? Press Ctrl+Shift+D again (3s)…",
                error=True,
            )
            self._discard_confirm_timer = self.set_timer(
                3.0, self._cancel_discard_confirm
            )
            return
        # Confirmed — execute discard
        self._awaiting_discard_confirm = False
        self._discard_confirm_timer.stop()
        self._execute_discard_quit()

    def _cancel_discard_confirm(self) -> None:
        self._awaiting_discard_confirm = False
        self._set_status("")

    def _execute_discard_quit(self) -> None:
        session_dir = self._session.session_dir if self._session else None
        self._shutdown_summary = self._collect_shutdown_metrics()

        # Phase 1 with discard flag — skips flush/metrics/end-header,
        # still does hardware teardown + session finalize (closes handles).
        self.cleanup_after_exit(include_ui=True, discard=True)

        # Soft-delete: move session to .discarded/ subfolder.
        if session_dir is not None and session_dir.exists():
            discarded_dir = session_dir.parent / ".discarded"
            discarded_dir.mkdir(exist_ok=True)
            dest = discarded_dir / session_dir.name
            try:
                session_dir.rename(dest)
                log.info("Discarded session → %s", dest)
            except Exception:
                log.exception("Failed to move session to .discarded/")

        self._discard_mode = True
        self._completed_session = None  # prevent Phase 2
        self.exit()

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

            n_seg = self._current_segment
            if n_seg > 1:
                lines.append(f"  Segments:    {n_seg}")

            try:
                for seg in range(1, n_seg + 1):
                    label = f" (seg {seg})" if n_seg > 1 else ""
                    mic_wav = self._session.audio_path_for_segment(seg)
                    mic_flac = mic_wav.with_suffix(".flac")
                    # Show FLAC if it exists (post-compression), otherwise WAV
                    for tag, flac, wav in [
                        ("Mic audio", mic_flac, mic_wav),
                        (
                            "Sys audio",
                            self._session.audio_sys_path_for_segment(seg).with_suffix(
                                ".flac"
                            ),
                            self._session.audio_sys_path_for_segment(seg),
                        ),
                    ]:
                        path = flac if flac.exists() else wav
                        if path.exists():
                            sz = path.stat().st_size / (1024 * 1024)
                            lines.append(f"  {tag}{label}:  {path.name} ({sz:.1f} MB)")
            except (OSError, TypeError):
                pass

            transcript_path = self._session.transcript_path
            if transcript_path.exists():
                size_kb = transcript_path.stat().st_size / 1024
                lines.append(f"  Transcript:  {transcript_path} ({size_kb:.1f} KB)")

        return "\n".join(lines)

    def _deferred_quit(self) -> None:
        if hasattr(self, "_discard_confirm_timer"):
            self._discard_confirm_timer.stop()
        self._stop_recording()
        self.exit()

    def _stop_recording(self) -> None:
        self.cleanup_after_exit(include_ui=True)

    def cleanup_after_exit(
        self, *, include_ui: bool = False, discard: bool = False
    ) -> None:
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

            # Capture session reference before any step can clear _session.
            if self._session is not None:
                self._completed_session = self._session

            # When discarding, prevent in-flight batch callbacks from
            # writing to a session we're about to move.
            if discard:
                self._ignore_batch_results = True

            # Steps skipped when discarding (data we're about to throw away).
            skip_on_discard = frozenset(
                {"flush_audio", "session_metrics", "session_end_header"}
            )

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
                (
                    "session_finalize",
                    lambda: self._cleanup_session_finalize(include_ui),
                ),
                ("reset_state", self._cleanup_reset_state),
            ]
            for name, step in steps:
                if discard and name in skip_on_discard:
                    continue
                try:
                    step()
                except Exception:
                    log.exception("Cleanup step %s failed", name)

    def post_exit_cleanup(self) -> None:
        """Phase 2 cleanup: compression and summarization. Runs after TUI exit."""
        session = self._completed_session
        if session is None:
            return
        session_dir = session.session_dir
        n_segments = self._current_segment

        for seg in range(1, n_segments + 1):
            label = f" (seg {seg})" if n_segments > 1 else ""
            print(f"  Compressing audio{label}…", flush=True)
            try:
                session.compress_audio_segment(seg)
            except Exception:
                log.exception("Failed to compress audio segment %d", seg)

            if self._sys_audio_enabled:
                print(f"  Compressing system audio{label}…", flush=True)
                try:
                    session.compress_sys_audio_segment(seg)
                except Exception:
                    log.exception("Failed to compress sys audio segment %d", seg)

        if not self._skip_summary:
            print("  Generating summary…", flush=True)
            try:
                from scarecrow.summarizer import summarize_session_segments

                result = summarize_session_segments(
                    session_dir,
                    n_segments,
                    obsidian_dir=self._cfg.OBSIDIAN_VAULT_DIR,
                )
                if result:
                    log.info("Summary: %s", result)
                    self._summary_path = result
            except Exception:
                log.exception("Failed to generate summary")
        else:
            print("  Summary skipped (quick quit).", flush=True)

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
        if self._sys_capture is not None:
            try:
                self._sys_capture.stop()
            except Exception:
                log.exception("Failed to stop system audio capture")

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
        self._sys_capture = None

    def _cleanup_shutdown_executor(self) -> None:
        if self._batch_executor is not None:
            # wait=True ensures the MLX executor thread exits cleanly before
            # the transcriber's model is released. wait=False was the original
            # code but left the thread alive, which caused crashes if a new
            # executor was subsequently created.
            self._batch_executor.shutdown(wait=True, cancel_futures=False)
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
