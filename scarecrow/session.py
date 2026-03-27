"""Session management — timestamped directories and transcript files."""

from datetime import datetime
from pathlib import Path


class Session:
    """Manages a recording session's files."""

    def __init__(self, base_dir: Path | None = None) -> None:
        if base_dir is None:
            base_dir = Path("recordings")

        now = datetime.now()
        timestamp = now.strftime("%Y-%m-%d_%H-%M-%S")
        self._session_dir = base_dir / timestamp
        self._session_dir.mkdir(parents=True, exist_ok=True)

        self._transcript_file = None
        self._finalized = False
        header_timestamp = now.strftime("%Y-%m-%d %H:%M:%S")
        self.append_sentence(f"Session Start: {header_timestamp}")

    @property
    def session_dir(self) -> Path:
        """Returns the session directory."""
        return self._session_dir

    @property
    def audio_path(self) -> Path:
        """Returns path to audio.wav in session dir."""
        return self._session_dir / "audio.wav"

    @property
    def transcript_path(self) -> Path:
        """Returns path to transcript.txt in session dir."""
        return self._session_dir / "transcript.txt"

    def append_sentence(self, text: str) -> None:
        """Appends a line to transcript.txt, flushes immediately."""
        if self._finalized:
            return
        if self._transcript_file is None:
            self._transcript_file = self.transcript_path.open("a", encoding="utf-8")

        self._transcript_file.write(text + "\n")
        self._transcript_file.flush()

    def write_end_header(self) -> None:
        """Write session end timestamp to transcript."""
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.append_sentence(f"Session End: {ts}")

    def finalize(self) -> None:
        """Closes any open file handles."""
        self._finalized = True
        if self._transcript_file is not None:
            self._transcript_file.close()
            self._transcript_file = None
