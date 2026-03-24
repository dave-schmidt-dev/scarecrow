"""Manual verification script — proves AudioRecorder and Transcriber can run
simultaneously on the same microphone on macOS.

Run with:
    uv run python scripts/test_dual_stream.py
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

from scarecrow.recorder import AudioRecorder
from scarecrow.session import Session
from scarecrow.transcriber import Transcriber

DURATION_SECONDS = 30


def main() -> None:
    session = Session(base_dir=Path("recordings"))

    recorder = AudioRecorder(session.audio_path)
    sentence_count = 0
    recorder_error: Exception | None = None
    transcriber_error: Exception | None = None

    def on_realtime_update(text: str) -> None:
        print(f"\r[live] {text}    ", end="", flush=True)

    def on_final_text(text: str) -> None:
        nonlocal sentence_count
        sentence_count += 1
        print(f"\n[final] {text}")

    transcriber = Transcriber(
        on_realtime_update=on_realtime_update,
        on_final_text=on_final_text,
    )

    # Start AudioRecorder (sounddevice)
    print("Starting AudioRecorder (sounddevice)…")
    try:
        recorder.start()
    except Exception as exc:
        recorder_error = exc
        print(f"ERROR: AudioRecorder failed to start: {exc}", file=sys.stderr)

    # Start Transcriber (RealtimeSTT/PyAudio)
    print("Starting Transcriber (RealtimeSTT)… (model load may take a moment)")
    try:
        transcriber.prepare()
    except Exception as exc:
        transcriber_error = exc
        print(f"ERROR: Transcriber failed to start: {exc}", file=sys.stderr)

    if recorder_error or transcriber_error:
        _print_summary(
            session=session,
            sentence_count=sentence_count,
            recorder_error=recorder_error,
            transcriber_error=transcriber_error,
        )
        return

    print(
        f"Both streams running. Speak into your mic… ({DURATION_SECONDS}s or Ctrl+C)\n"
    )

    # Run transcriber.text() in a background thread so the main thread can
    # handle the countdown and KeyboardInterrupt cleanly.
    stop_event = threading.Event()

    def transcription_loop() -> None:
        try:
            while not stop_event.is_set():
                transcriber.text()
        except Exception as exc:
            nonlocal transcriber_error
            transcriber_error = exc

    transcription_thread = threading.Thread(target=transcription_loop, daemon=True)
    transcription_thread.start()

    try:
        deadline = time.monotonic() + DURATION_SECONDS
        while time.monotonic() < deadline:
            remaining = int(deadline - time.monotonic())
            print(f"\r  {remaining:2d}s remaining…  ", end="", flush=True)
            time.sleep(1)
        print()  # newline after countdown
    except KeyboardInterrupt:
        print("\nCtrl+C received — stopping early.")

    # Tear down both streams
    stop_event.set()

    print("Stopping Transcriber…")
    try:
        transcriber.shutdown()
    except Exception as exc:
        if transcriber_error is None:
            transcriber_error = exc

    transcription_thread.join(timeout=5)

    print("Stopping AudioRecorder…")
    try:
        recorder.stop()
    except Exception as exc:
        if recorder_error is None:
            recorder_error = exc

    session.finalize()

    _print_summary(
        session=session,
        sentence_count=sentence_count,
        recorder_error=recorder_error,
        transcriber_error=transcriber_error,
    )


def _print_summary(
    *,
    session: Session,
    sentence_count: int,
    recorder_error: Exception | None,
    transcriber_error: Exception | None,
) -> None:
    wav_path = session.audio_path
    wav_size = wav_path.stat().st_size if wav_path.exists() else 0

    print("\n--- Summary ---")
    print(f"WAV file : {wav_path.resolve()}")
    print(f"WAV size : {wav_size:,} bytes")
    print(f"Sentences: {sentence_count}")

    failures: list[str] = []
    if recorder_error is not None:
        failures.append(f"AudioRecorder: {recorder_error}")
    if transcriber_error is not None:
        failures.append(f"Transcriber: {transcriber_error}")

    if failures:
        for reason in failures:
            print(f"FAIL: {reason}")
        sys.exit(1)
    else:
        print("PASS: Both streams coexisted without errors")


if __name__ == "__main__":
    main()
