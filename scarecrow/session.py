"""Session management — timestamped directories and transcript files."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from scarecrow.config import Config

log = logging.getLogger(__name__)


class Session:
    """Manages a recording session's files."""

    def __init__(
        self,
        base_dir: Path | None = None,
        *,
        cfg: Config | None = None,
    ) -> None:
        if base_dir is None:
            if cfg is not None:
                base_dir = cfg.DEFAULT_RECORDINGS_DIR
            else:
                base_dir = Path("recordings")

        now = datetime.now()
        timestamp = now.strftime("%Y-%m-%d_%H-%M-%S")
        self._session_dir = base_dir / timestamp
        self._session_dir.mkdir(parents=True, exist_ok=True)

        self._transcript_file = None
        self._finalized = False
        self._write_failed: bool = False
        self.append_event(
            {
                "type": "session_start",
                "schema_version": 1,
                "timestamp": now.isoformat(timespec="seconds"),
                "session_dir": str(self._session_dir),
            }
        )

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
        """Returns path to transcript.jsonl in session dir."""
        return self._session_dir / "transcript.jsonl"

    @property
    def write_failed(self) -> bool:
        """True if a transcript write has failed (e.g. disk full)."""
        return self._write_failed

    def append_event(self, event: dict) -> None:
        """Appends a JSON event to transcript.jsonl, flushes immediately."""
        if self._finalized or self._write_failed:
            return
        try:
            if self._transcript_file is None:
                self._transcript_file = self.transcript_path.open("a", encoding="utf-8")
            self._transcript_file.write(json.dumps(event) + "\n")
            self._transcript_file.flush()
        except OSError:
            log.exception("Failed to write to transcript file")
            self._write_failed = True

    def rename(self, name: str) -> None:
        """Rename the session directory by appending a slugified name."""
        slug = re.sub(r"[^a-z0-9-]", "", name.lower().replace(" ", "-"))
        slug = re.sub(r"-+", "-", slug).strip("-")[:60]
        if not slug:
            return

        # Close transcript file before rename
        if self._transcript_file is not None:
            self._transcript_file.flush()
            self._transcript_file.close()
            self._transcript_file = None

        # Rename directory
        timestamp_part = self._session_dir.name
        new_name = f"{timestamp_part}_{slug}"
        new_dir = self._session_dir.parent / new_name
        self._session_dir.rename(new_dir)
        self._session_dir = new_dir

        # Write rename event (lazy open will reopen transcript at new path)
        self.append_event(
            {
                "type": "session_renamed",
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "name": name,
                "slug": slug,
                "session_dir": str(self._session_dir),
            }
        )

    def compress_audio(self) -> Path | None:
        """Compress audio.wav to audio.flac (lossless).

        Returns FLAC path or None on failure.

        Note: reads the entire WAV into memory for compression. For a 2-hour
        session at 16kHz mono, this is ~230 MB. Acceptable on current hardware
        but could be switched to streaming compression if needed.
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
        ts = datetime.now().isoformat(timespec="seconds")
        self.append_event({"type": "session_end", "timestamp": ts})

    def finalize(self) -> None:
        """Closes any open file handles."""
        self._finalized = True
        if self._transcript_file is not None:
            self._transcript_file.close()
            self._transcript_file = None
