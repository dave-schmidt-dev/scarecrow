"""Manual verification script for dual-model streaming transcription.

Run with:
    uv run python scripts/test_transcription.py
"""

from __future__ import annotations

from scarecrow.transcriber import Transcriber


def on_realtime_update(text: str) -> None:
    print(f"\r[live] {text}    ", end="", flush=True)


def on_realtime_stabilized(text: str) -> None:
    print(f"\r[live/stable] {text}    ", end="", flush=True)


def on_final_text(text: str) -> None:
    # Print on its own line so final results stand out from live updates
    print(f"\n[final] {text}")


def main() -> None:
    transcriber = Transcriber(
        on_realtime_update=on_realtime_update,
        on_realtime_stabilized=on_realtime_stabilized,
        on_final_text=on_final_text,
    )

    print("Starting transcriber… (this may take a moment to load models)")
    transcriber.start()
    print("Speak into your microphone… (Ctrl+C to stop)\n")

    try:
        while True:
            transcriber.text()
    except KeyboardInterrupt:
        print("\n\nStopping…")
    finally:
        transcriber.shutdown()
        print("Done.")


if __name__ == "__main__":
    main()
