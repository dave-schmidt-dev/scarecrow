"""Manual verification script — records 5 seconds of audio.

Also demonstrates drain_buffer(), which the app uses for VAD-based batch transcription.

Run with:
    uv run python scripts/test_audio_capture.py
"""

import time
from pathlib import Path

from scarecrow.recorder import AudioRecorder
from scarecrow.session import Session


def main() -> None:
    session = Session(base_dir=Path("recordings"))
    recorder = AudioRecorder(session.audio_path)

    print("Recording for 5 seconds...")
    recorder.start()
    time.sleep(5)

    # Drain the in-memory buffer (simulating what the app does every 30s)
    audio = recorder.drain_buffer()
    if audio is not None:
        print(
            f"drain_buffer(): {len(audio)} samples "
            f"({len(audio) / recorder.sample_rate:.1f}s at {recorder.sample_rate}Hz)"
        )
    else:
        print("drain_buffer(): empty (no audio captured)")

    path = recorder.stop()
    print(f"Done. Saved to {path}")


if __name__ == "__main__":
    main()
