"""Manual verification script for live transcription pipeline.

Run with:
    uv run python scripts/test_transcription.py
"""

from __future__ import annotations

import time
from pathlib import Path

from scarecrow.recorder import AudioRecorder
from scarecrow.session import Session
from scarecrow.transcriber import Transcriber, TranscriberBindings


def on_realtime_update(text: str) -> None:
    print(f"\r[live] {text}    ", end="", flush=True)


def on_realtime_stabilized(text: str) -> None:
    print(f"\r[live/stable] {text}    ", end="", flush=True)


def main() -> None:
    transcriber = Transcriber(
        TranscriberBindings(
            on_realtime_update=on_realtime_update,
            on_realtime_stabilized=on_realtime_stabilized,
        )
    )

    print("Starting transcriber… (this may take a moment to load models)")
    transcriber.prepare()

    session = Session(base_dir=Path("recordings"))
    recorder = AudioRecorder(
        output_path=session.audio_path,
        on_audio=transcriber.accept_audio,
    )

    transcriber.begin_session()
    recorder.start()

    print("Speak into your microphone… (Ctrl+C to stop)\n")

    try:
        while True:
            time.sleep(0.1)
    except KeyboardInterrupt:
        print("\n\nStopping…")
    finally:
        recorder.stop()
        transcriber.end_session()
        transcriber.shutdown()
        session.finalize()
        print("Done.")


if __name__ == "__main__":
    main()
