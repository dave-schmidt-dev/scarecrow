"""Live captions via Apple's SFSpeechRecognizer (on-device, streaming).

The captioner owns its own AVAudioEngine tap for feeding audio to
SFSpeechRecognizer.  This is separate from the AudioRecorder used for
WAV capture and batch transcription — both can tap the same hardware
mic simultaneously.

All setup and teardown must happen on the main thread because
AVAudioEngine tap callbacks require the main run loop.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

log = logging.getLogger(__name__)

RealtimeCallback = Callable[[str], None]
ErrorCallback = Callable[[str, str], None]

SESSION_DURATION = 55  # seconds before rotating (Apple limit is ~60)

# Incremental-commit tuning.  Apple's formattedString() returns ALL text since
# the session started, so without early commits the partial grows to fill the
# entire pane before flushing on isFinal.  We commit words to stable in chunks,
# keeping only the trailing PARTIAL_TAIL words as a potentially-unstable partial.
_COMMIT_THRESHOLD = 10  # min uncommitted words before we flush a chunk to stable
_PARTIAL_TAIL = 4  # words kept as partial after each early commit


@dataclass(slots=True)
class CaptionerBindings:
    """Callbacks for live caption events."""

    on_realtime_update: RealtimeCallback | None = None
    on_realtime_stabilized: RealtimeCallback | None = None
    on_error: ErrorCallback | None = None


class LiveCaptioner:
    """Streaming live captions using Apple's Speech framework.

    Audio is captured via AVAudioEngine's input node tap and fed to
    SFSpeechAudioBufferRecognitionRequest.  Recognition result callbacks
    fire on the main run loop (pumped by Textual's event loop on macOS).

    The 1-minute recognition session limit is handled by transparent
    rotation: a new session starts every ~55 seconds.

    Important: begin_session() and end_session() must be called from
    the main thread.
    """

    def __init__(self, bindings: CaptionerBindings | None = None) -> None:
        self._bindings = bindings or CaptionerBindings()
        self._ready = False
        self._active = False

        self._recognizer = None
        self._audio_engine = None
        self._request = None
        self._task = None
        self._session_start: float = 0
        self._prev_text = ""
        self._tap_installed = False
        self._needs_restart = False  # set by result_handler, consumed by tick()
        self._committed_word_count = 0  # words from this session committed to stable

    def bind(self, bindings: CaptionerBindings) -> None:
        """Attach callbacks."""
        self._bindings = bindings

    @property
    def is_ready(self) -> bool:
        return self._ready

    @property
    def has_active_worker(self) -> bool:
        return self._active

    def prepare(self) -> None:
        """Request authorization and validate availability."""
        import Speech

        event = threading.Event()
        status_box: list[int] = []

        def handler(status: int) -> None:
            status_box.append(status)
            event.set()

        Speech.SFSpeechRecognizer.requestAuthorization_(handler)
        event.wait(timeout=30)

        if not status_box:
            msg = "Speech recognition authorization timed out"
            raise RuntimeError(msg)

        status = status_box[0]
        if status != Speech.SFSpeechRecognizerAuthorizationStatusAuthorized:
            msg = f"Speech recognition not authorized (status={status})"
            raise RuntimeError(msg)

        self._ready = True
        log.info("LiveCaptioner: speech recognition authorized")

    def begin_session(self) -> None:
        """Start the audio engine and recognition. Must be called from main thread."""
        if not self._ready:
            msg = "LiveCaptioner is not prepared"
            raise RuntimeError(msg)
        if self._active:
            return

        import AVFoundation
        import Speech
        from Foundation import NSLocale

        locale = NSLocale.localeWithLocaleIdentifier_("en-US")
        self._recognizer = Speech.SFSpeechRecognizer.alloc().initWithLocale_(locale)
        self._recognizer.setDefaultTaskHint_(
            Speech.SFSpeechRecognitionTaskHintDictation
        )

        if not self._recognizer.isAvailable():
            self._emit_error("captioner", "Speech recognizer is not available")
            return

        self._audio_engine = AVFoundation.AVAudioEngine.alloc().init()
        self._start_audio_engine()
        self._start_recognition_session()
        self._active = True
        log.info("LiveCaptioner: session started")

    def end_session(self) -> None:
        """Stop recognition and audio engine. Must be called from main thread."""
        if not self._active:
            return
        self._active = False
        self._needs_restart = False
        self._cleanup()
        log.info("LiveCaptioner: session ended")

    def shutdown(self, timeout: float | None = 5) -> None:
        """Stop everything and release resources."""
        self.end_session()
        self._ready = False
        self._recognizer = None

    def tick(self) -> None:
        """Pump the Cocoa run loop and rotate if needed.

        Must be called periodically from a timer (e.g. Textual set_interval).
        asyncio does not pump the Cocoa NSRunLoop, so recognition callbacks
        will not fire without this.

        Session restarts after a natural isFinal are deferred here rather than
        done inline inside the result handler, because creating a new
        recognitionTask from within an existing task's result callback causes
        reentrancy problems in Apple's Speech framework (hang then stale words).
        """
        if not self._active:
            return
        self._pump_runloop()
        self._tick_body()

    def _pump_runloop(self) -> None:
        """Run the main NSRunLoop briefly so recognition callbacks can fire."""
        from Foundation import NSDate, NSRunLoop

        NSRunLoop.currentRunLoop().runUntilDate_(
            NSDate.dateWithTimeIntervalSinceNow_(0.01)
        )

    def _tick_body(self) -> None:
        """Post-pump logic: handle deferred restart and 55s rotation."""
        if self._needs_restart:
            self._needs_restart = False
            self._request = None
            self._task = None
            self._start_recognition_session()
            return  # _session_start just reset; skip rotation check this tick

        elapsed = time.monotonic() - self._session_start
        if elapsed >= SESSION_DURATION:
            self._rotate_session()

    def _emit_error(self, source: str, message: str) -> None:
        log.error("%s: %s", source, message)
        if self._bindings.on_error is not None:
            self._bindings.on_error(source, message)

    def _start_audio_engine(self) -> None:
        """Install tap on the audio engine's input node and start it."""
        input_node = self._audio_engine.inputNode()
        fmt = input_node.outputFormatForBus_(0)
        log.info(
            "LiveCaptioner audio: %.0f Hz, %d ch",
            fmt.sampleRate(),
            fmt.channelCount(),
        )

        def tap_callback(buffer, _when):
            if self._request is not None:
                self._request.appendAudioPCMBuffer_(buffer)

        input_node.installTapOnBus_bufferSize_format_block_(0, 1024, fmt, tap_callback)
        self._tap_installed = True

        self._audio_engine.prepare()
        success, error = self._audio_engine.startAndReturnError_(None)
        if not success:
            msg = f"Audio engine failed to start: {error}"
            raise RuntimeError(msg)

    def _start_recognition_session(self) -> None:
        """Create a new recognition request and task."""
        import Speech

        request = Speech.SFSpeechAudioBufferRecognitionRequest.alloc().init()
        request.setShouldReportPartialResults_(True)
        request.setRequiresOnDeviceRecognition_(True)
        if hasattr(request, "setAddsPunctuation_"):
            request.setAddsPunctuation_(True)

        self._request = request
        self._session_start = time.monotonic()
        self._prev_text = ""
        self._committed_word_count = 0

        def result_handler(result, error):
            if error is not None:
                code = error.code()
                # 216 = session ended (expected during rotation)
                # 1110 = no speech detected (benign)
                if code not in (216, 1110):
                    desc = error.localizedDescription()
                    self._emit_error("captioner", str(desc))
                return

            if result is None:
                return

            text = result.bestTranscription().formattedString()
            is_final = result.isFinal()
            words = text.split()

            if is_final:
                # Commit any words not yet sent to stable.
                committed = min(self._committed_word_count, len(words))
                remaining = " ".join(words[committed:])
                if remaining and self._bindings.on_realtime_stabilized is not None:
                    self._bindings.on_realtime_stabilized(remaining)
                self._committed_word_count = 0
                self._prev_text = ""
                # Schedule restart via tick() — starting a new recognitionTask
                # inline from inside a result handler causes reentrancy problems.
                if self._active and self._request is request:
                    self._needs_restart = True
            else:
                # Incremental commit: Apple's formattedString() grows to contain
                # the entire session; flush settled words to stable in chunks so
                # the live pane scrolls rather than fills-and-clears.
                committed = min(
                    self._committed_word_count, max(0, len(words) - _PARTIAL_TAIL)
                )
                uncommitted = words[committed:]

                if len(uncommitted) >= _COMMIT_THRESHOLD + _PARTIAL_TAIL:
                    flush_end = len(words) - _PARTIAL_TAIL
                    chunk = " ".join(words[committed:flush_end])
                    if chunk and self._bindings.on_realtime_stabilized is not None:
                        self._bindings.on_realtime_stabilized(chunk)
                    self._committed_word_count = flush_end
                    partial = " ".join(words[-_PARTIAL_TAIL:])
                else:
                    partial = " ".join(uncommitted)

                if partial and self._bindings.on_realtime_update is not None:
                    self._bindings.on_realtime_update(partial)
                self._prev_text = text

        self._task = self._recognizer.recognitionTaskWithRequest_resultHandler_(
            request, result_handler
        )
        log.debug("LiveCaptioner: recognition session started")

    def _rotate_session(self) -> None:
        """End current session and start a new one."""
        log.debug("LiveCaptioner: rotating recognition session")
        self._needs_restart = False  # cancel any pending deferred restart
        if self._request is not None:
            self._request.endAudio()
        self._request = None
        self._task = None
        self._prev_text = ""
        self._start_recognition_session()

    def _cleanup(self) -> None:
        """Release audio engine and recognition resources."""
        if self._request is not None:
            try:
                self._request.endAudio()
            except Exception:
                log.debug("endAudio failed during cleanup", exc_info=True)
            self._request = None
        self._task = None

        if self._audio_engine is not None:
            if self._tap_installed:
                try:
                    self._audio_engine.inputNode().removeTapOnBus_(0)
                except Exception:
                    log.debug("removeTap failed during cleanup", exc_info=True)
                self._tap_installed = False
            try:
                self._audio_engine.stop()
            except Exception:
                log.debug("audio engine stop failed", exc_info=True)
            self._audio_engine = None

        self._prev_text = ""
        log.debug("LiveCaptioner: cleaned up")
