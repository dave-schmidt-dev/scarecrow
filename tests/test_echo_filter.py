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


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_threshold_boundary_just_above() -> None:
    """Jaccard similarity at exactly 0.6 must be detected as an echo."""
    # 3 shared out of 5 total unique words → Jaccard = 3/5 = 0.6
    ef = EchoFilter(similarity_threshold=0.6)
    ef.record_sys("alpha beta gamma")
    assert ef.is_echo("alpha beta gamma delta epsilon") is True


def test_threshold_boundary_just_below() -> None:
    """Jaccard similarity just below 0.6 must not be detected as an echo."""
    # 3 shared out of 6 total unique → Jaccard = 3/6 = 0.5
    ef = EchoFilter(similarity_threshold=0.6)
    ef.record_sys("alpha beta gamma")
    assert ef.is_echo("alpha beta gamma delta epsilon zeta") is False


def test_multiple_sys_entries_best_match_wins() -> None:
    """Echo should be detected if ANY recent sys entry matches."""
    ef = EchoFilter()
    ef.record_sys("completely unrelated words here today")
    ef.record_sys("also different content about something")
    ef.record_sys("the quick brown fox jumps over the lazy dog")
    assert ef.is_echo("the quick brown fox jumps over the lazy dog") is True


def test_prune_on_record_removes_expired() -> None:
    """Recording a new entry should prune all expired entries."""
    ef = EchoFilter(window_seconds=5.0)

    base_time = 1000.0
    with patch("scarecrow.echo_filter.time.monotonic", return_value=base_time):
        for i in range(20):
            ef.record_sys(f"entry {i} alpha beta gamma delta")

    assert len(ef._recent_sys) == 20

    # Advance time past window and record one more
    with patch("scarecrow.echo_filter.time.monotonic", return_value=base_time + 10.0):
        ef.record_sys("fresh entry alpha beta gamma delta")

    # Only the fresh entry should survive
    assert len(ef._recent_sys) == 1


# ---------------------------------------------------------------------------
# Consecutive-run echo detection (partial overlap / offset boundaries)
# ---------------------------------------------------------------------------


def test_consecutive_run_catches_partial_echo() -> None:
    """Mic echoing 8+ consecutive sys words is suppressed even with low Jaccard."""
    ef = EchoFilter(min_consecutive_run=8)
    ef.record_sys(
        "highway underpasses and other conspicuous locations of course "
        "philly isn't the only city that deals with graffiti"
    )
    # Mic picks up a shifted window — different start, same middle
    assert (
        ef.is_echo(
            "some unrelated preamble words here but of course "
            "philly isn't the only city that deals with graffiti "
            "and some trailing words too"
        )
        is True
    )


def test_consecutive_run_ignores_short_matches() -> None:
    """Fewer than min_consecutive_run matching words is not suppressed."""
    ef = EchoFilter(min_consecutive_run=8)
    ef.record_sys("the quick brown fox jumps over the lazy dog")
    # Only 4 consecutive matches — below threshold
    assert (
        ef.is_echo(
            "totally different intro the quick brown fox and then diverges completely"
        )
        is False
    )


def test_consecutive_run_own_speech_not_suppressed() -> None:
    """User's own speech should not be suppressed even when sys is active."""
    ef = EchoFilter(min_consecutive_run=8)
    ef.record_sys(
        "and the quarterly results show a fifteen percent increase "
        "in revenue compared to the previous fiscal year"
    )
    # User responding to the remote speaker — completely different words
    assert (
        ef.is_echo(
            "yeah I think we should focus on the cost reduction "
            "strategy before the next board meeting on friday"
        )
        is False
    )


def test_consecutive_run_with_common_words() -> None:
    """Common words alone should not trigger a false positive."""
    ef = EchoFilter(min_consecutive_run=8)
    ef.record_sys(
        "the project is on track and we will deliver the results "
        "by the end of the quarter as planned"
    )
    # Different sentence that shares some common words but not consecutively
    assert (
        ef.is_echo(
            "I think the timeline is too aggressive and we need "
            "to push the deadline by at least two weeks"
        )
        is False
    )


# ---------------------------------------------------------------------------
# Bidirectional echo detection (sys suppressed when mic transcribed first)
# ---------------------------------------------------------------------------


def test_sys_echo_suppressed_when_mic_first() -> None:
    """Sys text is suppressed if mic already transcribed the same content."""
    ef = EchoFilter()
    ef.record_mic("the quick brown fox jumps over the lazy dog")
    assert ef.is_sys_echo("the quick brown fox jumps over the lazy dog") is True


def test_sys_echo_not_suppressed_for_different_text() -> None:
    """Sys text passes through if it doesn't match recent mic text."""
    ef = EchoFilter()
    ef.record_mic("hello this is my own speech about something")
    assert (
        ef.is_sys_echo(
            "the remote speaker is talking about a completely different topic right now"
        )
        is False
    )


def test_sys_echo_consecutive_run_catches_partial() -> None:
    """Sys text with 8+ consecutive words matching mic is suppressed."""
    ef = EchoFilter(min_consecutive_run=8)
    ef.record_mic(
        "big meta social media trial brian mccullough from "
        "the tech room ride home will talk about the latest tech news"
    )
    # Sys drains later with overlapping but shifted content
    assert (
        ef.is_sys_echo(
            "big met a social media trial brian mccullough from "
            "the tech crew ride home we'll talk about the latest tech news"
        )
        is True
    )


def test_bidirectional_first_source_wins() -> None:
    """Whichever source transcribes first is kept; the other is suppressed."""
    ef = EchoFilter()
    text = "the quick brown fox jumps over the lazy dog in the park"

    # Scenario 1: mic first → sys suppressed
    ef.record_mic(text)
    assert ef.is_sys_echo(text) is True

    # Scenario 2: sys first → mic suppressed
    ef2 = EchoFilter()
    ef2.record_sys(text)
    assert ef2.is_echo(text) is True
