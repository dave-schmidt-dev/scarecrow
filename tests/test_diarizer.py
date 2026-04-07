"""Tests for the speaker diarization module."""

from __future__ import annotations

import json
from pathlib import Path

from scarecrow.diarizer import (
    SpeakersInfo,
    _diarization_path,
    _extract_segment_offsets,
    _find_speaker_at,
    _map_speaker_label,
    find_speakers_note,
    format_speakers_confirmation,
    label_events,
    parse_speakers_note,
)

# ---------------------------------------------------------------------------
# parse_speakers_note
# ---------------------------------------------------------------------------


class TestParseSpeakersNote:
    def test_mic_and_sys(self) -> None:
        info = parse_speakers_note("mic:Dave sys:Mike,Justin")
        assert info.mic_speakers == ["Dave"]
        assert info.sys_speakers == ["Mike", "Justin"]

    def test_sys_only(self) -> None:
        info = parse_speakers_note("sys:Mike,Justin")
        assert info.mic_speakers == []
        assert info.sys_speakers == ["Mike", "Justin"]

    def test_mic_only(self) -> None:
        info = parse_speakers_note("mic:Dave")
        assert info.mic_speakers == ["Dave"]
        assert info.sys_speakers == []

    def test_bare_names_in_person(self) -> None:
        info = parse_speakers_note("Dave,Sarah,Mike")
        assert info.mic_speakers == ["Dave", "Sarah", "Mike"]
        assert info.sys_speakers == []

    def test_bare_names_space_separated(self) -> None:
        info = parse_speakers_note("Dave Sarah Mike")
        assert info.mic_speakers == ["Dave", "Sarah", "Mike"]
        assert info.sys_speakers == []

    def test_mixed_bare_and_prefix(self) -> None:
        """If mic: prefix is present, bare names are ignored."""
        info = parse_speakers_note("mic:Dave sys:Mike ExtraName")
        assert info.mic_speakers == ["Dave"]
        assert info.sys_speakers == ["Mike"]

    def test_empty_string(self) -> None:
        info = parse_speakers_note("")
        assert info.mic_speakers == []
        assert info.sys_speakers == []

    def test_whitespace_only(self) -> None:
        info = parse_speakers_note("   ")
        assert info.mic_speakers == []
        assert info.sys_speakers == []

    def test_case_insensitive_prefix(self) -> None:
        info = parse_speakers_note("MIC:Dave SYS:Mike")
        assert info.mic_speakers == ["Dave"]
        assert info.sys_speakers == ["Mike"]

    def test_trailing_comma_ignored(self) -> None:
        info = parse_speakers_note("sys:Mike,Justin,")
        assert info.sys_speakers == ["Mike", "Justin"]

    def test_bare_names_strips_and_conjunction(self) -> None:
        info = parse_speakers_note("Jordan and Dan")
        assert info.mic_speakers == ["Jordan", "Dan"]

    def test_bare_names_strips_ampersand(self) -> None:
        info = parse_speakers_note("Jordan & Dan")
        assert info.mic_speakers == ["Jordan", "Dan"]


# ---------------------------------------------------------------------------
# find_speakers_note
# ---------------------------------------------------------------------------


class TestFindSpeakersNote:
    def test_finds_last_speakers_note(self) -> None:
        events = [
            {"type": "note", "tag": "SPEAKERS", "text": "sys:Alice"},
            {"type": "transcript", "text": "hello"},
            {"type": "note", "tag": "SPEAKERS", "text": "sys:Bob,Carol"},
        ]
        info = find_speakers_note(events)
        assert info is not None
        assert info.sys_speakers == ["Bob", "Carol"]

    def test_returns_none_when_no_speakers(self) -> None:
        events = [
            {"type": "note", "tag": "TASK", "text": "do something"},
            {"type": "transcript", "text": "hello"},
        ]
        assert find_speakers_note(events) is None

    def test_returns_none_for_empty_list(self) -> None:
        assert find_speakers_note([]) is None


# ---------------------------------------------------------------------------
# format_speakers_confirmation
# ---------------------------------------------------------------------------


class TestFormatSpeakersConfirmation:
    def test_mic_and_sys(self) -> None:
        info = SpeakersInfo(mic_speakers=["Dave"], sys_speakers=["Mike", "Justin"])
        assert (
            format_speakers_confirmation(info)
            == "Speakers set — mic: Dave | sys: Mike, Justin"
        )

    def test_sys_only(self) -> None:
        info = SpeakersInfo(sys_speakers=["Mike"])
        assert format_speakers_confirmation(info) == "Speakers set — sys: Mike"

    def test_empty(self) -> None:
        info = SpeakersInfo()
        assert format_speakers_confirmation(info) == "No speakers set"


# ---------------------------------------------------------------------------
# _extract_segment_offsets
# ---------------------------------------------------------------------------


class TestExtractSegmentOffsets:
    def test_single_segment(self) -> None:
        events = [{"type": "transcript", "elapsed": 10}]
        assert _extract_segment_offsets(events, 1) == [0]

    def test_two_segments(self) -> None:
        events = [
            {"type": "transcript", "elapsed": 10},
            {"type": "segment_boundary", "segment": 2, "elapsed": 3600},
            {"type": "transcript", "elapsed": 3610},
        ]
        assert _extract_segment_offsets(events, 2) == [0, 3600]

    def test_pads_missing_boundaries(self) -> None:
        events = [{"type": "transcript", "elapsed": 10}]
        offsets = _extract_segment_offsets(events, 3)
        assert len(offsets) == 3
        assert offsets[0] == 0


# ---------------------------------------------------------------------------
# _diarization_path
# ---------------------------------------------------------------------------


class TestDiarizationPath:
    def test_sys_seg1(self, tmp_path: Path) -> None:
        assert (
            _diarization_path(tmp_path, "sys", 1) == tmp_path / "diarization_sys.json"
        )

    def test_sys_seg2(self, tmp_path: Path) -> None:
        assert (
            _diarization_path(tmp_path, "sys", 2)
            == tmp_path / "diarization_sys_seg2.json"
        )

    def test_mic_seg1(self, tmp_path: Path) -> None:
        assert (
            _diarization_path(tmp_path, "mic", 1) == tmp_path / "diarization_mic.json"
        )


# ---------------------------------------------------------------------------
# _find_speaker_at
# ---------------------------------------------------------------------------


class TestFindSpeakerAt:
    def test_exact_match(self) -> None:
        segments = [
            {"start": 0.0, "end": 5.0, "speaker": "SPEAKER_00"},
            {"start": 5.5, "end": 10.0, "speaker": "SPEAKER_01"},
        ]
        assert _find_speaker_at(2.5, segments) == "SPEAKER_00"
        assert _find_speaker_at(7.0, segments) == "SPEAKER_01"

    def test_gap_finds_nearest(self) -> None:
        segments = [
            {"start": 0.0, "end": 5.0, "speaker": "SPEAKER_00"},
            {"start": 8.0, "end": 10.0, "speaker": "SPEAKER_01"},
        ]
        # 5.5 is closer to SPEAKER_00 (gap of 0.5 from midpoint 2.5? no, nearest mid)
        # Actually: mid of first is 2.5, mid of second is 9.0
        # 5.5 is 3.0 from first mid, 3.5 from second mid → SPEAKER_00
        assert _find_speaker_at(5.5, segments) == "SPEAKER_00"

    def test_too_far_returns_none(self) -> None:
        segments = [
            {"start": 0.0, "end": 1.0, "speaker": "SPEAKER_00"},
        ]
        # 20.0 is more than 5s from midpoint 0.5
        assert _find_speaker_at(20.0, segments) is None

    def test_empty_segments(self) -> None:
        assert _find_speaker_at(5.0, []) is None


# ---------------------------------------------------------------------------
# _map_speaker_label
# ---------------------------------------------------------------------------


class TestMapSpeakerLabel:
    def test_maps_by_name_when_counts_match(self) -> None:
        result = _map_speaker_label(
            "SPEAKER_00",
            speaker_names=["Mike", "Justin"],
            all_labels=["SPEAKER_00", "SPEAKER_01"],
        )
        assert result == "Mike"

    def test_maps_second_speaker(self) -> None:
        result = _map_speaker_label(
            "SPEAKER_01",
            speaker_names=["Mike", "Justin"],
            all_labels=["SPEAKER_00", "SPEAKER_01"],
        )
        assert result == "Justin"

    def test_generic_label_when_count_mismatch(self) -> None:
        result = _map_speaker_label(
            "SPEAKER_00",
            speaker_names=["Mike"],  # 1 name, 2 labels
            all_labels=["SPEAKER_00", "SPEAKER_01"],
        )
        assert result == "Speaker A"

    def test_generic_label_when_no_names(self) -> None:
        result = _map_speaker_label(
            "SPEAKER_01",
            speaker_names=[],
            all_labels=["SPEAKER_00", "SPEAKER_01"],
        )
        assert result == "Speaker B"

    def test_empty_labels(self) -> None:
        result = _map_speaker_label("SPEAKER_00", [], [])
        assert result == "SPEAKER_00"


# ---------------------------------------------------------------------------
# label_events (integration with sidecar JSON)
# ---------------------------------------------------------------------------


class TestLabelEvents:
    def _write_diarization(
        self, session_dir: Path, channel: str, segment: int, data: dict
    ) -> None:
        path = _diarization_path(session_dir, channel, segment)
        path.write_text(json.dumps(data), encoding="utf-8")

    def test_labels_sys_transcript_events(self, tmp_path: Path) -> None:
        """Sys transcript events get speaker labels from diarization."""
        self._write_diarization(
            tmp_path,
            "sys",
            1,
            {
                "version": 1,
                "channel": "sys",
                "segment": 1,
                "model": "pyannote/speaker-diarization-3.1",
                "speaker_names": ["Mike", "Justin"],
                "mic_speaker": "Dave",
                "segment_elapsed_offset": 0,
                "segments": [
                    {"start": 0.0, "end": 10.0, "speaker": "SPEAKER_00"},
                    {"start": 10.5, "end": 20.0, "speaker": "SPEAKER_01"},
                ],
            },
        )

        events = [
            {
                "type": "transcript",
                "elapsed": 5,
                "text": "Hello there",
                "source": "sys",
            },
            {"type": "transcript", "elapsed": 15, "text": "Hi back", "source": "sys"},
            {
                "type": "transcript",
                "elapsed": 8,
                "text": "My mic text",
                "source": "mic",
            },
        ]

        labeled = label_events(events, tmp_path, segment=1, segment_elapsed_offset=0)

        assert labeled[0]["speaker"] == "Mike"
        assert labeled[1]["speaker"] == "Justin"
        # When sys is the diarized channel, mic events should NOT be labeled
        # — they're likely speaker bleed, not the mic user talking.
        assert "speaker" not in labeled[2]

    def test_mic_labeled_when_mic_is_diarized_channel(self, tmp_path: Path) -> None:
        """In-person meeting: mic is diarized, so mic events get speaker labels."""
        self._write_diarization(
            tmp_path,
            "mic",
            1,
            {
                "version": 1,
                "channel": "mic",
                "segment": 1,
                "model": "pyannote/speaker-diarization-3.1",
                "speaker_names": ["Dave", "Sarah"],
                "mic_speaker": "Dave",
                "segment_elapsed_offset": 0,
                "segments": [
                    {"start": 0.0, "end": 10.0, "speaker": "SPEAKER_00"},
                    {"start": 10.5, "end": 20.0, "speaker": "SPEAKER_01"},
                ],
            },
        )

        events = [
            {"type": "transcript", "elapsed": 5, "text": "Hello", "source": "mic"},
        ]
        labeled = label_events(events, tmp_path, segment=1, segment_elapsed_offset=0)
        assert labeled[0]["speaker"] == "Dave"

    def test_no_diarization_file_returns_unchanged(self, tmp_path: Path) -> None:
        """Without diarization files, events pass through unchanged."""
        events = [
            {"type": "transcript", "elapsed": 5, "text": "Hello", "source": "mic"},
        ]
        labeled = label_events(events, tmp_path, segment=1, segment_elapsed_offset=0)
        assert "speaker" not in labeled[0]

    def test_non_transcript_events_pass_through(self, tmp_path: Path) -> None:
        """Non-transcript events are returned without modification."""
        self._write_diarization(
            tmp_path,
            "sys",
            1,
            {
                "version": 1,
                "channel": "sys",
                "segment": 1,
                "speaker_names": [],
                "mic_speaker": None,
                "segment_elapsed_offset": 0,
                "segments": [],
            },
        )

        events = [
            {"type": "note", "tag": "TASK", "text": "do thing"},
            {"type": "divider", "elapsed": 60},
        ]
        labeled = label_events(events, tmp_path, segment=1, segment_elapsed_offset=0)
        assert labeled[0]["type"] == "note"
        assert labeled[1]["type"] == "divider"
        assert "speaker" not in labeled[0]

    def test_segment_offset_applied(self, tmp_path: Path) -> None:
        """Elapsed offset is subtracted to get audio position."""
        self._write_diarization(
            tmp_path,
            "sys",
            2,
            {
                "version": 1,
                "channel": "sys",
                "segment": 2,
                "speaker_names": ["Alice"],
                "mic_speaker": None,
                "segment_elapsed_offset": 3600,
                "segments": [
                    {"start": 0.0, "end": 30.0, "speaker": "SPEAKER_00"},
                ],
            },
        )

        events = [
            {
                "type": "transcript",
                "elapsed": 3610,
                "text": "After boundary",
                "source": "sys",
            },
        ]
        labeled = label_events(events, tmp_path, segment=2, segment_elapsed_offset=3600)
        assert labeled[0]["speaker"] == "Alice"

    def test_original_events_not_mutated(self, tmp_path: Path) -> None:
        """label_events must not modify the original event dicts."""
        self._write_diarization(
            tmp_path,
            "sys",
            1,
            {
                "version": 1,
                "channel": "sys",
                "segment": 1,
                "speaker_names": ["Mike"],
                "mic_speaker": "Dave",
                "segment_elapsed_offset": 0,
                "segments": [
                    {"start": 0.0, "end": 10.0, "speaker": "SPEAKER_00"},
                ],
            },
        )

        events = [
            {"type": "transcript", "elapsed": 5, "text": "Hi", "source": "sys"},
        ]
        label_events(events, tmp_path, segment=1, segment_elapsed_offset=0)
        assert "speaker" not in events[0]


# ---------------------------------------------------------------------------
# Diarization JSON schema validation
# ---------------------------------------------------------------------------


class TestDiarizationJsonSchema:
    def test_sidecar_has_required_fields(self, tmp_path: Path) -> None:
        """Verify the sidecar JSON schema matches what label_events expects."""
        sidecar = {
            "version": 1,
            "channel": "sys",
            "segment": 1,
            "model": "pyannote/speaker-diarization-3.1",
            "device": "mps",
            "num_speakers_hint": 2,
            "speaker_names": ["Mike", "Justin"],
            "mic_speaker": "Dave",
            "segment_elapsed_offset": 0,
            "wall_time_seconds": 36.2,
            "segments": [
                {"start": 2.04, "end": 13.29, "speaker": "SPEAKER_00"},
                {"start": 13.62, "end": 15.64, "speaker": "SPEAKER_01"},
            ],
        }
        path = _diarization_path(tmp_path, "sys", 1)
        path.write_text(json.dumps(sidecar), encoding="utf-8")

        # Verify it can be consumed by label_events
        events = [
            {"type": "transcript", "elapsed": 5, "text": "test", "source": "sys"},
        ]
        labeled = label_events(events, tmp_path, segment=1, segment_elapsed_offset=0)
        assert labeled[0].get("speaker") == "Mike"
