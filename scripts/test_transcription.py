"""Manual verification script for dual-model streaming transcription.

Architecture note: Transcriber.prepare() initializes the AudioToTextRecorder,
then recorder.start() begins continuous capture. Callbacks drive the live pane
— there is no blocking text() loop.

Run with:
    uv run python scripts/test_transcription.py
"""

from __future__ import annotations

import os
import signal
import time


def on_realtime_update(text: str) -> None:
    print(f"\r[live] {text}    ", end="", flush=True)


def on_realtime_stabilized(text: str) -> None:
    print(f"\r[live/stable] {text}    ", end="", flush=True)


def main() -> None:
    from scarecrow.transcriber import Transcriber

    transcriber = Transcriber(
        on_realtime_update=on_realtime_update,
        on_realtime_stabilized=on_realtime_stabilized,
    )

    print("Starting transcriber… (this may take a moment to load models)")
    transcriber.prepare()

    assert transcriber.recorder is not None
    transcriber.recorder.start()

    print("Speak into your microphone… (Ctrl+C to stop)\n")

    try:
        while True:
            time.sleep(0.1)
    except KeyboardInterrupt:
        print("\n\nStopping…")
    finally:
        transcriber.shutdown()
        print("Done.")
        # Force exit — RealtimeSTT daemon threads can hang on join
        os.kill(os.getpid(), signal.SIGKILL)


if __name__ == "__main__":
    main()
