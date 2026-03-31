"""Tests for the EchoFilter transcript-level echo suppression."""

from __future__ import annotations

from unittest.mock import patch

from scarecrow.echo_filter import EchoFilter


def test_identical_text_is_echo() -> None:
    """Identical mic and sys transcripts must be detected as an echo."""
    ef = EchoFilter()
    ef.record_sys("the quick brown fox jumps over the lazy dog")
    assert ef.is_echo("the quick brown fox jumps over the lazy dog") is True


def test_high_overlap_is_echo() -> None:
    """Same sentence with one word different still exceeds similarity threshold."""
    ef = EchoFilter()
    ef.record_sys("the quick brown fox jumps over the lazy dog")
    # One word swapped — Jaccard similarity remains high
    assert ef.is_echo("the quick brown fox jumps over the lazy cat") is True


def test_different_text_not_echo() -> None:
    """Completely different text must not be detected as an echo."""
    ef = EchoFilter()
    ef.record_sys("hello world this is a test sentence")
    result = ef.is_echo("completely unrelated words about something else entirely")
    assert result is False


def test_short_text_not_echo() -> None:
    """Mic text with fewer than 3 words must never be flagged as an echo."""
    ef = EchoFilter()
    ef.record_sys("hello world")
    # Exact match, but under the 3-word minimum
    assert ef.is_echo("hello world") is False
    assert ef.is_echo("hello") is False
    assert ef.is_echo("") is False


def test_entries_expire() -> None:
    """Sys entries older than window_seconds must not match."""
    ef = EchoFilter(window_seconds=5.0)

    # Record sys at t=0; is_echo checked at t=10 (past the 5s window)
    base_time = 1000.0

    with patch("scarecrow.echo_filter.time.monotonic", return_value=base_time):
        ef.record_sys("the quick brown fox jumps over the lazy dog")

    # Advance time beyond the window
    with patch("scarecrow.echo_filter.time.monotonic", return_value=base_time + 10.0):
        result = ef.is_echo("the quick brown fox jumps over the lazy dog")

    assert result is False


def test_empty_sys_ignored() -> None:
    """Recording empty sys text must not cause errors or phantom matches."""
    ef = EchoFilter()
    ef.record_sys("")
    ef.record_sys("   ")
    # No valid entries stored — mic text should not be flagged
    assert ef.is_echo("the quick brown fox jumps over the lazy dog") is False


def test_no_sys_entries_returns_false() -> None:
    """is_echo with no recorded sys text must return False."""
    ef = EchoFilter()
    assert ef.is_echo("the quick brown fox jumps over the lazy dog") is False
