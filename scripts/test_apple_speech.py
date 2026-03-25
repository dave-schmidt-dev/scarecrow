#!/usr/bin/env python3
"""Phase 1 proof of concept: Apple SFSpeechRecognizer streaming from microphone.

Run with: .venv/bin/python scripts/test_apple_speech.py

Success criteria:
  - Words appear incrementally as spoken (not in batches)
  - Latency < 500ms from speech to first word
  - Works fully offline
  - Runs for > 2 minutes with session rotation

Press Ctrl+C to stop.
"""

from __future__ import annotations

import signal
import sys
import threading
import time

import AVFoundation
import Speech
from Foundation import NSDate, NSLocale, NSRunLoop


def request_authorization() -> bool:
    """Block until speech recognition authorization is granted or denied."""
    event = threading.Event()
    status_box: list[int] = []

    def handler(status: int) -> None:
        status_box.append(status)
        event.set()

    Speech.SFSpeechRecognizer.requestAuthorization_(handler)
    event.wait(timeout=30)

    if not status_box:
        print("Authorization timed out", file=sys.stderr)
        return False

    status = status_box[0]
    if status == Speech.SFSpeechRecognizerAuthorizationStatusAuthorized:
        print("Speech recognition authorized")
        return True

    names = {
        Speech.SFSpeechRecognizerAuthorizationStatusDenied: "denied",
        Speech.SFSpeechRecognizerAuthorizationStatusRestricted: "restricted",
        Speech.SFSpeechRecognizerAuthorizationStatusNotDetermined: "not determined",
    }
    print(f"Authorization: {names.get(status, status)}", file=sys.stderr)
    return False


class StreamingRecognizer:
    """Wraps SFSpeechRecognizer for continuous streaming recognition."""

    SESSION_DURATION = 55  # seconds before rotating

    def __init__(self) -> None:
        locale = NSLocale.localeWithLocaleIdentifier_("en-US")
        self._recognizer = Speech.SFSpeechRecognizer.alloc().initWithLocale_(locale)
        self._recognizer.setDefaultTaskHint_(
            Speech.SFSpeechRecognitionTaskHintDictation
        )
        self._audio_engine = AVFoundation.AVAudioEngine.alloc().init()
        self._request: Speech.SFSpeechAudioBufferRecognitionRequest | None = None
        self._task: Speech.SFSpeechRecognitionTask | None = None
        self._running = False
        self._session_start: float = 0
        self._last_final_text = ""
        self._tap_installed = False
        self._prev_text = ""

    def start(self) -> None:
        """Start the audio engine and first recognition session."""
        self._running = True
        self._start_session()
        self._start_audio_engine()
        print("\nListening... (Ctrl+C to stop)\n")

    def _start_audio_engine(self) -> None:
        """Install tap and start the audio engine."""
        input_node = self._audio_engine.inputNode()
        fmt = input_node.outputFormatForBus_(0)
        print(f"Audio format: {fmt.sampleRate():.0f} Hz, {fmt.channelCount()} ch")

        def tap_callback(buffer, when):
            if self._request is not None:
                self._request.appendAudioPCMBuffer_(buffer)

        input_node.installTapOnBus_bufferSize_format_block_(0, 1024, fmt, tap_callback)
        self._tap_installed = True

        self._audio_engine.prepare()
        success, error = self._audio_engine.startAndReturnError_(None)
        if not success:
            print(f"Audio engine failed to start: {error}", file=sys.stderr)
            sys.exit(1)

    def _start_session(self) -> None:
        """Start a new recognition session."""
        self._request = Speech.SFSpeechAudioBufferRecognitionRequest.alloc().init()
        self._request.setShouldReportPartialResults_(True)
        self._request.setRequiresOnDeviceRecognition_(True)
        if hasattr(self._request, "addsPunctuation"):
            self._request.setAddsPunctuation_(True)

        self._session_start = time.monotonic()

        def result_handler(result, error):
            if error is not None:
                code = error.code()
                # 216 = session ended (expected during rotation)
                # 1110 = no speech detected (benign)
                if code not in (216, 1110):
                    domain = error.domain()
                    desc = error.localizedDescription()
                    print(f"\n[error {domain}/{code}] {desc}", file=sys.stderr)
                return

            if result is None:
                return

            text = result.bestTranscription().formattedString()
            is_final = result.isFinal()

            prev = self._prev_text
            # Find where the new text diverges from what we already printed
            common = 0
            for i in range(min(len(prev), len(text))):
                if prev[i] == text[i]:
                    common = i + 1
                else:
                    break

            if common < len(prev):
                # Text was revised — erase back to divergence point
                erase = len(prev) - common
                sys.stdout.write("\b \b" * erase)

            # Print from divergence point onward
            sys.stdout.write(text[common:])
            sys.stdout.flush()
            self._prev_text = text

            if is_final:
                sys.stdout.write("\n")
                sys.stdout.flush()
                self._last_final_text = text
                self._prev_text = ""

        self._task = self._recognizer.recognitionTaskWithRequest_resultHandler_(
            self._request, result_handler
        )

    def check_rotation(self) -> None:
        """Rotate the recognition session if it's been running too long."""
        if not self._running:
            return
        elapsed = time.monotonic() - self._session_start
        if elapsed >= self.SESSION_DURATION:
            self._rotate_session()

    def _rotate_session(self) -> None:
        """End current session and start a new one."""
        if self._request is not None:
            self._request.endAudio()
        self._request = None
        self._task = None
        self._start_session()

    def stop(self) -> None:
        """Stop recognition and audio engine."""
        self._running = False

        if self._request is not None:
            self._request.endAudio()
            self._request = None
        self._task = None

        if self._tap_installed:
            self._audio_engine.inputNode().removeTapOnBus_(0)
            self._tap_installed = False
        self._audio_engine.stop()

        print("\n\nStopped.")


def main() -> None:
    if not request_authorization():
        sys.exit(1)

    recognizer = StreamingRecognizer()
    recognizer.start()

    # Run loop on main thread to receive Cocoa callbacks
    stop = threading.Event()

    def sigint_handler(sig, frame):
        stop.set()

    signal.signal(signal.SIGINT, sigint_handler)

    run_loop = NSRunLoop.currentRunLoop()
    while not stop.is_set():
        # Pump the run loop for 0.1s at a time
        run_loop.runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.1))
        recognizer.check_rotation()

    recognizer.stop()


if __name__ == "__main__":
    main()
