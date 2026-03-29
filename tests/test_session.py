"""Unit tests for Session."""

import json
import re
import time
from pathlib import Path

import pytest

from scarecrow.session import Session


def _read_jsonl(path: Path) -> list[dict]:
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    return [json.loads(line) for line in lines]


def test_creates_session_directory(tmp_path: Path) -> None:
    """Session creates a subdirectory inside base_dir."""
    session = Session(base_dir=tmp_path)
    assert session.session_dir.exists()
    assert session.session_dir.is_dir()
    assert session.session_dir.parent == tmp_path
    session.finalize()


def test_directory_name_format(tmp_path: Path) -> None:
    """Session directory name follows YYYY-MM-DD_HH-MM-SS format."""
    session = Session(base_dir=tmp_path)
    name = session.session_dir.name
    pattern = r"^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}$"
    assert re.match(pattern, name), (
        f"Directory name {name!r} does not match expected format"
    )
    session.finalize()


def test_audio_path(tmp_path: Path) -> None:
    """audio_path returns audio.wav inside the session directory."""
    session = Session(base_dir=tmp_path)
    assert session.audio_path == session.session_dir / "audio.wav"
    assert session.audio_path.name == "audio.wav"
    session.finalize()


def test_transcript_path(tmp_path: Path) -> None:
    """transcript_path returns transcript.jsonl inside the session directory."""
    session = Session(base_dir=tmp_path)
    assert session.transcript_path == session.session_dir / "transcript.jsonl"
    assert session.transcript_path.name == "transcript.jsonl"
    session.finalize()


def test_append_event_creates_file(tmp_path: Path) -> None:
    """append_event creates transcript.jsonl; session_start event at init creates it."""
    session = Session(base_dir=tmp_path)
    # File is created at init time when the session_start event is written
    assert session.transcript_path.exists()
    session.append_event({"type": "transcript", "text": "Hello world"})
    assert session.transcript_path.exists()
    session.finalize()


def test_append_event_content(tmp_path: Path) -> None:
    """append_event writes a JSON line after the session_start event."""
    session = Session(base_dir=tmp_path)
    session.append_event({"type": "transcript", "text": "Hello world"})
    events = _read_jsonl(session.transcript_path)
    # First event is session_start; second is the appended event
    assert events[1] == {"type": "transcript", "text": "Hello world"}
    session.finalize()


def test_multiple_appends_one_per_line(tmp_path: Path) -> None:
    """Multiple appends produce one JSON object per line, after the session_start."""
    session = Session(base_dir=tmp_path)
    payloads = [
        {"type": "transcript", "text": "First sentence."},
        {"type": "transcript", "text": "Second sentence."},
        {"type": "transcript", "text": "Third sentence."},
    ]
    for p in payloads:
        session.append_event(p)
    events = _read_jsonl(session.transcript_path)
    # First event is session_start; remaining events are the appended ones
    assert events[1:] == payloads
    session.finalize()


def test_append_flushes_immediately(tmp_path: Path) -> None:
    """append_event flushes so content is readable without closing."""
    session = Session(base_dir=tmp_path)
    session.append_event({"type": "transcript", "text": "Flushed line"})
    # Read file while it's still open (file handle not yet closed)
    content = session.transcript_path.read_text(encoding="utf-8")
    assert "Flushed line" in content
    session.finalize()


def test_finalize_closes_cleanly(tmp_path: Path) -> None:
    """finalize closes the file handle without error."""
    session = Session(base_dir=tmp_path)
    session.append_event({"type": "transcript", "text": "Some text"})
    session.finalize()
    # Calling finalize again should not raise
    session.finalize()


def test_finalize_without_appending(tmp_path: Path) -> None:
    """finalize works cleanly even if no events were appended beyond session_start."""
    session = Session(base_dir=tmp_path)
    session.finalize()  # Should not raise


def test_default_base_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Default base_dir is ./recordings relative to cwd."""
    monkeypatch.chdir(tmp_path)
    session = Session()
    assert session.session_dir.resolve().parent == tmp_path / "recordings"
    session.finalize()


def test_unique_directories_for_different_sessions(tmp_path: Path) -> None:
    """Two sessions created at different times get different directories."""
    session1 = Session(base_dir=tmp_path)
    time.sleep(1.1)  # Ensure the timestamp differs by at least one second
    session2 = Session(base_dir=tmp_path)
    assert session1.session_dir != session2.session_dir
    session1.finalize()
    session2.finalize()


def test_session_header_is_first_event(tmp_path: Path) -> None:
    """The first event in the transcript is a session_start JSON object."""
    session = Session(base_dir=tmp_path)
    session.finalize()
    events = _read_jsonl(session.transcript_path)
    assert len(events) >= 1
    assert events[0]["type"] == "session_start"


def test_session_header_format(tmp_path: Path) -> None:
    """The session_start event has a timestamp in ISO 8601 format and session_dir."""
    session = Session(base_dir=tmp_path)
    session.finalize()
    events = _read_jsonl(session.transcript_path)
    first = events[0]
    assert first["type"] == "session_start"
    pattern = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$"
    assert re.match(pattern, first["timestamp"]), (
        f"session_start timestamp {first['timestamp']!r} does not match ISO 8601"
    )
    assert "session_dir" in first


# ---------------------------------------------------------------------------
# New tests: transcript file integrity (disk I/O)
# ---------------------------------------------------------------------------


def test_transcript_file_contains_all_appended_events(tmp_path: Path) -> None:
    """All events appended to a session must appear in the file on disk."""
    session = Session(base_dir=tmp_path)
    texts = [
        "First sentence here.",
        "Second sentence here.",
        "Third sentence here.",
    ]
    for t in texts:
        session.append_event({"type": "transcript", "text": t})
    session.finalize()

    content = session.transcript_path.read_text(encoding="utf-8")
    for t in texts:
        assert t in content, f"Expected text {t!r} not found in transcript"


def test_transcript_file_has_session_start_event(tmp_path: Path) -> None:
    """The transcript file must start with a session_start event."""
    session = Session(base_dir=tmp_path)
    session.append_event({"type": "transcript", "text": "Some content."})
    session.finalize()

    events = _read_jsonl(session.transcript_path)
    assert events[0]["type"] == "session_start", (
        f"First event must be session_start; got: {events[0]!r}"
    )


def test_transcript_file_has_session_end_event(tmp_path: Path) -> None:
    """write_end_header writes a session_end event."""
    session = Session(base_dir=tmp_path)
    session.append_event({"type": "transcript", "text": "Some content."})
    session.write_end_header()
    session.finalize()

    events = _read_jsonl(session.transcript_path)
    end_events = [e for e in events if e.get("type") == "session_end"]
    assert end_events, (
        "Transcript file must contain a session_end event after write_end_header()"
    )


def test_transcript_session_end_event_format(tmp_path: Path) -> None:
    """session_end event must have an ISO 8601 timestamp."""
    session = Session(base_dir=tmp_path)
    session.write_end_header()
    session.finalize()

    events = _read_jsonl(session.transcript_path)
    end_events = [e for e in events if e.get("type") == "session_end"]
    assert end_events, "No session_end event found in transcript"
    pattern = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$"
    assert re.match(pattern, end_events[0]["timestamp"]), (
        f"session_end timestamp {end_events[0]['timestamp']!r} does not match ISO 8601"
    )


def test_transcript_content_order(tmp_path: Path) -> None:
    """Events must appear in the file in the order they were appended."""
    session = Session(base_dir=tmp_path)
    session.append_event({"type": "transcript", "text": "Alpha."})
    session.append_event({"type": "transcript", "text": "Beta."})
    session.append_event({"type": "transcript", "text": "Gamma."})
    session.finalize()

    content = session.transcript_path.read_text(encoding="utf-8")
    alpha_pos = content.index("Alpha.")
    beta_pos = content.index("Beta.")
    gamma_pos = content.index("Gamma.")
    assert alpha_pos < beta_pos < gamma_pos, (
        "Events must appear in the file in the order they were appended"
    )


# ---------------------------------------------------------------------------
# Session I/O failure handling
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# FLAC compression
# ---------------------------------------------------------------------------


def test_compress_audio_creates_flac(tmp_path: Path) -> None:
    """compress_audio must create a FLAC file and remove the WAV."""
    session = Session(base_dir=tmp_path)

    import numpy as np
    import soundfile as sf

    audio = np.zeros(16000, dtype=np.float32)  # 1 second of silence
    sf.write(session.audio_path, audio, 16000)
    assert session.audio_path.exists()

    result = session.compress_audio()

    assert result is not None
    assert result.suffix == ".flac"
    assert result.exists()
    assert not session.audio_path.exists()  # WAV deleted
    session.finalize()


def test_compress_audio_returns_none_when_no_wav(tmp_path: Path) -> None:
    """compress_audio must return None when no WAV file exists."""
    session = Session(base_dir=tmp_path)
    # Don't create any audio file
    result = session.compress_audio()
    assert result is None
    session.finalize()


def test_final_audio_path_prefers_flac(tmp_path: Path) -> None:
    """final_audio_path must return FLAC path when it exists."""
    session = Session(base_dir=tmp_path)

    import numpy as np
    import soundfile as sf

    audio = np.zeros(16000, dtype=np.float32)
    sf.write(session.audio_path, audio, 16000)
    session.compress_audio()

    assert session.final_audio_path.suffix == ".flac"
    session.finalize()


def test_final_audio_path_falls_back_to_wav(tmp_path: Path) -> None:
    """final_audio_path must return WAV path when no FLAC exists."""
    session = Session(base_dir=tmp_path)
    assert session.final_audio_path == session.audio_path
    session.finalize()


def test_rename_session(tmp_path: Path) -> None:
    """rename() must rename the session directory and update paths."""
    session = Session(base_dir=tmp_path)
    old_dir = session.session_dir

    session.rename("Huddle with Mike")

    assert not old_dir.exists()
    assert session.session_dir.exists()
    assert "huddle-with-mike" in session.session_dir.name
    assert old_dir.name in session.session_dir.name  # timestamp preserved
    session.finalize()


def test_rename_writes_event(tmp_path: Path) -> None:
    """rename() must write a session_renamed event."""
    session = Session(base_dir=tmp_path)
    session.rename("My Meeting")
    session.finalize()

    events = [
        json.loads(line)
        for line in session.transcript_path.read_text().strip().splitlines()
    ]
    renamed = [e for e in events if e["type"] == "session_renamed"]
    assert len(renamed) == 1
    assert renamed[0]["name"] == "My Meeting"
    assert renamed[0]["slug"] == "my-meeting"


def test_rename_slug_sanitization(tmp_path: Path) -> None:
    """rename() must sanitize special characters in the name."""
    session = Session(base_dir=tmp_path)
    session.rename("Q4 Planning: Budget & Goals!!!")

    assert "q4-planning-budget-goals" in session.session_dir.name
    session.finalize()


def test_rename_empty_name_is_noop(tmp_path: Path) -> None:
    """rename() with empty name must not rename."""
    session = Session(base_dir=tmp_path)
    old_dir = session.session_dir
    session.rename("")
    assert session.session_dir == old_dir
    session.finalize()


def test_append_event_handles_open_failure(tmp_path: Path) -> None:
    """append_event must catch OSError from open() and set write_failed."""
    from unittest.mock import patch

    session = Session(base_dir=tmp_path)
    # Close the transcript file so the internal handle is None.
    session.finalize()
    # Reset internal state so the next append_event call tries to open again.
    session._finalized = False
    session._transcript_file = None

    # Patch the builtin open used inside pathlib so that Path.open raises.
    original_open = Path.open

    def failing_open(self, *args, **kwargs):
        if self == session.transcript_path:
            raise OSError("permission denied")
        return original_open(self, *args, **kwargs)

    with patch.object(Path, "open", failing_open):
        session.append_event(
            {"type": "transcript", "text": "this should fail silently"}
        )

    assert session.write_failed is True, (
        "write_failed must be True after open() raises OSError"
    )
