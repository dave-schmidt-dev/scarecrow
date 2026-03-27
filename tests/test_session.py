"""Unit tests for Session."""

import re
import time
from pathlib import Path

import pytest

from scarecrow.session import Session


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
    """transcript_path returns transcript.txt inside the session directory."""
    session = Session(base_dir=tmp_path)
    assert session.transcript_path == session.session_dir / "transcript.txt"
    assert session.transcript_path.name == "transcript.txt"
    session.finalize()


def test_append_sentence_creates_file(tmp_path: Path) -> None:
    """append_sentence creates transcript.txt; header write at init creates it."""
    session = Session(base_dir=tmp_path)
    # File is created at init time when the session header is written
    assert session.transcript_path.exists()
    session.append_sentence("Hello world")
    assert session.transcript_path.exists()
    session.finalize()


def test_append_sentence_content(tmp_path: Path) -> None:
    """append_sentence writes the text followed by a newline, after the header."""
    session = Session(base_dir=tmp_path)
    session.append_sentence("Hello world")
    lines = session.transcript_path.read_text(encoding="utf-8").splitlines()
    # First line is the session header; second line is the appended sentence
    assert lines[1] == "Hello world"
    session.finalize()


def test_multiple_appends_one_per_line(tmp_path: Path) -> None:
    """Multiple appends produce one sentence per line, after the header."""
    session = Session(base_dir=tmp_path)
    sentences = ["First sentence.", "Second sentence.", "Third sentence."]
    for s in sentences:
        session.append_sentence(s)
    lines = session.transcript_path.read_text(encoding="utf-8").splitlines()
    # First line is the session header; remaining lines are the appended sentences
    assert lines[1:] == sentences
    session.finalize()


def test_append_flushes_immediately(tmp_path: Path) -> None:
    """append_sentence flushes so content is readable without closing."""
    session = Session(base_dir=tmp_path)
    session.append_sentence("Flushed line")
    # Read file while it's still open (file handle not yet closed)
    content = session.transcript_path.read_text(encoding="utf-8")
    assert "Flushed line" in content
    session.finalize()


def test_finalize_closes_cleanly(tmp_path: Path) -> None:
    """finalize closes the file handle without error."""
    session = Session(base_dir=tmp_path)
    session.append_sentence("Some text")
    session.finalize()
    # Calling finalize again should not raise
    session.finalize()


def test_finalize_without_appending(tmp_path: Path) -> None:
    """finalize works cleanly even if no sentences were appended."""
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


def test_session_header_is_first_line(tmp_path: Path) -> None:
    """The first line of the transcript starts with 'Session Start: '."""
    session = Session(base_dir=tmp_path)
    session.finalize()
    lines = session.transcript_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) >= 1
    assert lines[0].startswith("Session Start: ")


def test_session_header_format(tmp_path: Path) -> None:
    """The session header matches 'Session Start: YYYY-MM-DD HH:MM:SS'."""
    session = Session(base_dir=tmp_path)
    session.finalize()
    first_line = session.transcript_path.read_text(encoding="utf-8").splitlines()[0]
    pattern = r"^Session Start: \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$"
    assert re.match(pattern, first_line), (
        f"Header line {first_line!r} does not match expected format"
    )


# ---------------------------------------------------------------------------
# New tests: transcript file integrity (disk I/O)
# ---------------------------------------------------------------------------


def test_transcript_file_contains_all_appended_sentences(tmp_path: Path) -> None:
    """All sentences appended to a session must appear in the file on disk."""
    session = Session(base_dir=tmp_path)
    sentences = [
        "First sentence here.",
        "Second sentence here.",
        "Third sentence here.",
    ]
    for s in sentences:
        session.append_sentence(s)
    session.finalize()

    content = session.transcript_path.read_text(encoding="utf-8")
    for s in sentences:
        assert s in content, f"Expected sentence {s!r} not found in transcript"


def test_transcript_file_has_session_start_header(tmp_path: Path) -> None:
    """The transcript file must start with a 'Session Start:' header line."""
    session = Session(base_dir=tmp_path)
    session.append_sentence("Some content.")
    session.finalize()

    lines = session.transcript_path.read_text(encoding="utf-8").splitlines()
    assert lines[0].startswith("Session Start: "), (
        f"First line must be a Session Start header; got: {lines[0]!r}"
    )


def test_transcript_file_has_session_end_header(tmp_path: Path) -> None:
    """write_end_header writes a Session End footer."""
    session = Session(base_dir=tmp_path)
    session.append_sentence("Some content.")
    session.write_end_header()
    session.finalize()

    content = session.transcript_path.read_text(encoding="utf-8")
    assert "Session End: " in content, (
        "Transcript file must contain a Session End header after write_end_header()"
    )


def test_transcript_session_end_header_format(tmp_path: Path) -> None:
    """Session End header must match 'Session End: YYYY-MM-DD HH:MM:SS'."""
    session = Session(base_dir=tmp_path)
    session.write_end_header()
    session.finalize()

    lines = session.transcript_path.read_text(encoding="utf-8").splitlines()
    end_lines = [ln for ln in lines if ln.startswith("Session End: ")]
    assert end_lines, "No 'Session End:' line found in transcript"
    pattern = r"^Session End: \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$"
    assert re.match(pattern, end_lines[0]), (
        f"Session End line {end_lines[0]!r} does not match expected format"
    )


def test_transcript_content_order(tmp_path: Path) -> None:
    """Sentences must appear in the file in the order they were appended."""
    session = Session(base_dir=tmp_path)
    session.append_sentence("Alpha.")
    session.append_sentence("Beta.")
    session.append_sentence("Gamma.")
    session.finalize()

    content = session.transcript_path.read_text(encoding="utf-8")
    alpha_pos = content.index("Alpha.")
    beta_pos = content.index("Beta.")
    gamma_pos = content.index("Gamma.")
    assert alpha_pos < beta_pos < gamma_pos, (
        "Sentences must appear in the file in the order they were appended"
    )
