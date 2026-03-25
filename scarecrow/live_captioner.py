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
        self._cleanup()
        log.info("LiveCaptioner: session ended")

    def shutdown(self, timeout: float | None = 5) -> None:
        """Stop everything and release resources."""
        self.end_session()
        self._ready = False
        self._recognizer = None

    def check_rotation(self) -> None:
        """Rotate the recognition session if approaching the time limit.

        Call this periodically from a timer (e.g. Textual set_interval).
        """
        if not self._active:
            return
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

        self._request = Speech.SFSpeechAudioBufferRecognitionRequest.alloc().init()
        self._request.setShouldReportPartialResults_(True)
        self._request.setRequiresOnDeviceRecognition_(True)
        if hasattr(self._request, "setAddsPunctuation_"):
            self._request.setAddsPunctuation_(True)

        self._session_start = time.monotonic()
        self._prev_text = ""

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

            if is_final:
                if text and self._bindings.on_realtime_stabilized is not None:
                    self._bindings.on_realtime_stabilized(text)
                self._prev_text = ""
            else:
                if text and self._bindings.on_realtime_update is not None:
                    self._bindings.on_realtime_update(text)
                self._prev_text = text

        self._task = self._recognizer.recognitionTaskWithRequest_resultHandler_(
            self._request, result_handler
        )
        log.debug("LiveCaptioner: recognition session started")

    def _rotate_session(self) -> None:
        """End current session and start a new one."""
        log.debug("LiveCaptioner: rotating recognition session")
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
