"""Manual verification script — records 5 seconds of audio."""

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
    path = recorder.stop()

    print(f"Done. Saved to {path}")


if __name__ == "__main__":
    main()
