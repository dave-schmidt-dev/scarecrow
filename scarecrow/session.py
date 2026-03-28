"""Session management — timestamped directories and transcript files."""

import logging
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)


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
        self._write_failed: bool = False
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
    def final_audio_path(self) -> Path:
        """Returns the audio file path — FLAC if compressed, WAV otherwise."""
        flac = self._session_dir / "audio.flac"
        if flac.exists():
            return flac
        return self.audio_path

    @property
    def transcript_path(self) -> Path:
        """Returns path to transcript.txt in session dir."""
        return self._session_dir / "transcript.txt"

    @property
    def write_failed(self) -> bool:
        """True if a transcript write has failed (e.g. disk full)."""
        return self._write_failed

    def append_sentence(self, text: str) -> None:
        """Appends a line to transcript.txt, flushes immediately."""
        if self._finalized:
            return
        try:
            if self._transcript_file is None:
                self._transcript_file = self.transcript_path.open("a", encoding="utf-8")
            self._transcript_file.write(text + "\n")
            self._transcript_file.flush()
        except OSError:
            log.exception("Failed to write to transcript file")
            self._write_failed = True

    def compress_audio(self) -> Path | None:
        """Compress audio.wav to audio.flac (lossless).

        Returns FLAC path or None on failure.
        """
        import soundfile as sf

        wav_path = self.audio_path
        if not wav_path.exists():
            return None

        flac_path = wav_path.with_suffix(".flac")
        try:
            data, samplerate = sf.read(wav_path)
            sf.write(flac_path, data, samplerate, format="FLAC")
            wav_path.unlink()
            log.info("Compressed %s → %s", wav_path.name, flac_path.name)
            return flac_path
        except Exception:
            log.exception("Failed to compress audio to FLAC")
            # Keep the WAV if compression fails
            if flac_path.exists():
                flac_path.unlink(missing_ok=True)
            return None

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
