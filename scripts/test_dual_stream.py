"""Manual verification script — proves AudioRecorder and Transcriber can run
simultaneously on the same microphone on macOS.

Run with:
    uv run python scripts/test_dual_stream.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from scarecrow.recorder import AudioRecorder
from scarecrow.session import Session
from scarecrow.transcriber import Transcriber, TranscriberBindings

DURATION_SECONDS = 30


def main() -> None:
    session = Session(base_dir=Path("recordings"))

    live_update_count = 0
    recorder_error: Exception | None = None
    transcriber_error: Exception | None = None

    def on_realtime_update(text: str) -> None:
        nonlocal live_update_count
        live_update_count += 1
        print(f"\r[live] {text}    ", end="", flush=True)

    def on_realtime_stabilized(text: str) -> None:
        print(f"\r[stable] {text}    ", end="", flush=True)

    print("Starting Transcriber… (model load may take a moment)")
    transcriber = Transcriber(
        TranscriberBindings(
            on_realtime_update=on_realtime_update,
            on_realtime_stabilized=on_realtime_stabilized,
        )
    )
    try:
        transcriber.prepare()
    except Exception as exc:
        transcriber_error = exc
        print(f"ERROR: Transcriber failed to start: {exc}", file=sys.stderr)

    print("Starting AudioRecorder (sounddevice)…")
    recorder = AudioRecorder(
        output_path=session.audio_path,
        on_audio=transcriber.accept_audio,
    )
    try:
        recorder.start()
    except Exception as exc:
        recorder_error = exc
        print(f"ERROR: AudioRecorder failed to start: {exc}", file=sys.stderr)

    if recorder_error or transcriber_error:
        _print_summary(
            session=session,
            live_update_count=live_update_count,
            recorder_error=recorder_error,
            transcriber_error=transcriber_error,
        )
        return

    transcriber.begin_session()

    print(
        f"Both streams running. Speak into your mic… ({DURATION_SECONDS}s or Ctrl+C)\n"
    )

    try:
        deadline = time.monotonic() + DURATION_SECONDS
        while time.monotonic() < deadline:
            remaining = int(deadline - time.monotonic())
            print(f"\r  {remaining:2d}s remaining…  ", end="", flush=True)
            time.sleep(1)
        print()
    except KeyboardInterrupt:
        print("\nCtrl+C received — stopping early.")

    audio = recorder.drain_buffer()
    if audio is not None and len(audio) > 0:
        print(
            f"\n[batch] Drained {len(audio)} samples from buffer "
            f"({len(audio) / recorder.sample_rate:.1f}s of audio)"
        )
    else:
        print("\n[batch] No audio in buffer (nothing spoken?)")

    print("Stopping Transcriber…")
    try:
        transcriber.end_session()
        transcriber.shutdown()
    except Exception as exc:
        if transcriber_error is None:
            transcriber_error = exc

    print("Stopping AudioRecorder…")
    try:
        recorder.stop()
    except Exception as exc:
        if recorder_error is None:
            recorder_error = exc

    session.finalize()

    _print_summary(
        session=session,
        live_update_count=live_update_count,
        recorder_error=recorder_error,
        transcriber_error=transcriber_error,
    )


def _print_summary(
    *,
    session: Session,
    live_update_count: int,
    recorder_error: Exception | None,
    transcriber_error: Exception | None,
) -> None:
    wav_path = session.audio_path
    wav_size = wav_path.stat().st_size if wav_path.exists() else 0

    print("\n--- Summary ---")
    print(f"WAV file     : {wav_path.resolve()}")
    print(f"WAV size     : {wav_size:,} bytes")
    print(f"Live updates : {live_update_count}")

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
