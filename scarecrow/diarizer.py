"""Speaker diarization — post-session pipeline for speaker-attributed transcripts.

Uses pyannote-audio (optional dependency) to assign speaker labels to transcript
events.  All diarization logic lives here: execution, JSON I/O, and consumption
by the summarizer via ``label_events()``.

Install: ``uv sync --extra diarization``
"""

from __future__ import annotations

import json
import logging
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from scarecrow import config

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# /speakers parsing
# ---------------------------------------------------------------------------


@dataclass
class SpeakersInfo:
    """Parsed result of a /speakers command."""

    mic_speakers: list[str] = field(default_factory=list)
    sys_speakers: list[str] = field(default_factory=list)


def parse_speakers_note(text: str) -> SpeakersInfo:
    """Parse a /speakers note text into structured speaker info.

    Syntax:
        mic:Dave sys:Mike,Justin   → mic=["Dave"], sys=["Mike", "Justin"]
        sys:Mike,Justin            → mic=[], sys=["Mike", "Justin"]
        mic:Dave                   → mic=["Dave"], sys=[]
        Dave,Sarah,Mike            → mic=["Dave", "Sarah", "Mike"], sys=[]
    """
    info = SpeakersInfo()
    tokens = text.strip().split()
    bare_names: list[str] = []

    for token in tokens:
        lower = token.lower()
        if lower.startswith("mic:"):
            names = token[4:]
            info.mic_speakers = [n.strip() for n in names.split(",") if n.strip()]
        elif lower.startswith("sys:"):
            names = token[4:]
            info.sys_speakers = [n.strip() for n in names.split(",") if n.strip()]
        else:
            # Bare names — treat as mic speakers (in-person fallback)
            bare_names.extend(n.strip() for n in token.split(",") if n.strip())

    if bare_names and not info.mic_speakers:
        info.mic_speakers = bare_names

    return info


def find_speakers_note(events: list[dict]) -> SpeakersInfo | None:
    """Find the last SPEAKERS note in a list of events.

    Returns None if no /speakers command was used in the session.
    """
    last_text: str | None = None
    for event in events:
        if event.get("type") == "note" and event.get("tag") == "SPEAKERS":
            last_text = event.get("text", "")
    if last_text is None:
        return None
    return parse_speakers_note(last_text)


def format_speakers_confirmation(info: SpeakersInfo) -> str:
    """Format a human-readable confirmation string for the /speakers command."""
    parts: list[str] = []
    if info.mic_speakers:
        parts.append(f"mic: {', '.join(info.mic_speakers)}")
    if info.sys_speakers:
        parts.append(f"sys: {', '.join(info.sys_speakers)}")
    if not parts:
        return "No speakers set"
    return f"Speakers set — {' | '.join(parts)}"


# ---------------------------------------------------------------------------
# JSONL reader (standalone — no scarecrow imports needed beyond config)
# ---------------------------------------------------------------------------


def _read_events(transcript_path: Path) -> list[dict]:
    """Read events from a transcript.jsonl file."""
    events: list[dict] = []
    with transcript_path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return events


# ---------------------------------------------------------------------------
# Segment elapsed offsets
# ---------------------------------------------------------------------------


def _extract_segment_offsets(events: list[dict], n_segments: int) -> list[int]:
    """Extract the elapsed offset for each segment.

    Segment 1 offset is always 0.  Segment N offset is the elapsed value
    from the segment_boundary event where segment == N.
    """
    offsets = [0]
    for event in events:
        if event.get("type") == "segment_boundary":
            offsets.append(int(event.get("elapsed", 0)))
    # Pad if fewer boundaries than expected
    while len(offsets) < n_segments:
        offsets.append(offsets[-1] if offsets else 0)
    return offsets[:n_segments]


# ---------------------------------------------------------------------------
# Diarization execution
# ---------------------------------------------------------------------------

# JSON sidecar schema version
_SCHEMA_VERSION = 1


def _diarization_path(session_dir: Path, channel: str, segment: int) -> Path:
    """Build the sidecar JSON path for a given channel and segment."""
    if segment == 1:
        return session_dir / f"diarization_{channel}.json"
    return session_dir / f"diarization_{channel}_seg{segment}.json"


def _audio_path(session_dir: Path, channel: str, segment: int) -> Path:
    """Build the audio file path for a given channel and segment."""
    if channel == "mic":
        if segment == 1:
            for suffix in (".flac", ".wav"):
                p = session_dir / f"audio{suffix}"
                if p.exists():
                    return p
        else:
            for suffix in (".flac", ".wav"):
                p = session_dir / f"audio_seg{segment}{suffix}"
                if p.exists():
                    return p
    else:  # sys
        if segment == 1:
            for suffix in (".flac", ".wav"):
                p = session_dir / f"audio_sys{suffix}"
                if p.exists():
                    return p
        else:
            for suffix in (".flac", ".wav"):
                p = session_dir / f"audio_sys_seg{segment}{suffix}"
                if p.exists():
                    return p
    return session_dir / f"audio_{channel}_NOT_FOUND"


def _prepare_mono_audio(audio_path: Path, tmp_dir: Path) -> Path:
    """Convert stereo audio to mono WAV in a temp directory.

    Returns the original path if already mono.
    """
    import soundfile as sf

    info = sf.info(str(audio_path))
    if info.channels <= 1:
        return audio_path

    data, sr = sf.read(str(audio_path), dtype="float32", always_2d=True)
    if data.shape[1] > 1:
        data = data.mean(axis=1, keepdims=True)

    tmp_path = tmp_dir / f"{audio_path.stem}_mono.wav"
    sf.write(str(tmp_path), data, sr, subtype="FLOAT")
    return tmp_path


def _load_pipeline(model_id: str, device: str):
    """Load pyannote diarization pipeline. Returns (pipeline, device_used)."""
    import os

    import torch
    from pyannote.audio import Pipeline

    # Allow HF Hub network access for model download
    old_offline = os.environ.pop("HF_HUB_OFFLINE", None)
    try:
        hf_token = os.environ.get("HF_TOKEN")
        pipeline = Pipeline.from_pretrained(model_id, token=hf_token)
    finally:
        if old_offline is not None:
            os.environ["HF_HUB_OFFLINE"] = old_offline

    if device == "mps":
        try:
            pipeline.to(torch.device("mps"))
            return pipeline, "mps"
        except RuntimeError:
            log.warning("MPS failed, falling back to CPU for diarization")
            pipeline.to(torch.device("cpu"))
            return pipeline, "cpu"

    return pipeline, device


def _run_diarization(
    pipeline,
    audio_path: Path,
    num_speakers: int | None,
) -> list[dict]:
    """Run diarization on one audio file, return list of segment dicts."""
    kwargs: dict = {}
    if num_speakers is not None:
        kwargs["num_speakers"] = num_speakers

    output = pipeline(str(audio_path), **kwargs)

    # pyannote 4.0 returns DiarizeOutput; extract the Annotation
    annotation = getattr(output, "speaker_diarization", output)

    segments: list[dict] = []
    for turn, _, speaker in annotation.itertracks(yield_label=True):
        segments.append(
            {
                "start": round(turn.start, 3),
                "end": round(turn.end, 3),
                "speaker": speaker,
            }
        )
    return segments


def _cleanup_diarization_files(session_dir: Path) -> None:
    """Delete all diarization sidecar files from a session directory."""
    import contextlib

    for f in session_dir.glob("diarization_*.json"):
        with contextlib.suppress(OSError):
            f.unlink()


def diarize_session(
    session_dir: Path,
    n_segments: int,
    events: list[dict],
    sys_audio_enabled: bool,
    progress_callback: Callable[[str], None] | None = None,
) -> bool:
    """Run post-session diarization on a completed session.

    Returns True if diarization was performed, False if skipped.
    On failure, cleans up partial output so the summarizer sees either
    complete diarization or none.
    """
    speakers = find_speakers_note(events)
    if speakers is None:
        log.info("No /speakers note found; skipping diarization")
        return False

    if not speakers.mic_speakers and not speakers.sys_speakers:
        log.info("Empty /speakers note; skipping diarization")
        return False

    # Determine what to diarize
    # sys speakers + sys audio → diarize sys only, mic = user's name
    # mic speakers only (in-person) → diarize mic
    diarize_channel: str | None = None
    num_speakers: int | None = None

    if speakers.sys_speakers and sys_audio_enabled:
        diarize_channel = "sys"
        num_speakers = len(speakers.sys_speakers)
    elif speakers.mic_speakers and not speakers.sys_speakers:
        diarize_channel = "mic"
        num_speakers = len(speakers.mic_speakers)
    else:
        log.info("No matching audio channel for speakers; skipping diarization")
        return False

    offsets = _extract_segment_offsets(events, n_segments)
    model_id = config.DIARIZATION_MODEL
    device = config.DIARIZATION_DEVICE

    def _progress(msg: str) -> None:
        log.info(msg)
        if progress_callback:
            progress_callback(msg)

    _progress(f"Loading diarization model ({model_id})…")

    try:
        pipeline, device_used = _load_pipeline(model_id, device)
    except Exception:
        log.exception("Failed to load diarization pipeline")
        return False

    try:
        with tempfile.TemporaryDirectory(prefix="scarecrow_diar_") as tmp_dir:
            tmp_path = Path(tmp_dir)

            for seg in range(1, n_segments + 1):
                seg_label = f" seg {seg}" if n_segments > 1 else ""
                _progress(f"Diarizing {diarize_channel}{seg_label}…")

                audio = _audio_path(session_dir, diarize_channel, seg)
                if not audio.exists():
                    log.warning("Audio file not found: %s", audio)
                    continue

                mono_audio = _prepare_mono_audio(audio, tmp_path)

                t0 = time.monotonic()
                segments = _run_diarization(pipeline, mono_audio, num_speakers)
                wall_time = time.monotonic() - t0

                # Write sidecar JSON
                sidecar = {
                    "version": _SCHEMA_VERSION,
                    "channel": diarize_channel,
                    "segment": seg,
                    "model": model_id,
                    "device": device_used,
                    "num_speakers_hint": num_speakers,
                    "speaker_names": (
                        speakers.sys_speakers
                        if diarize_channel == "sys"
                        else speakers.mic_speakers
                    ),
                    "mic_speaker": (
                        speakers.mic_speakers[0] if speakers.mic_speakers else None
                    ),
                    "segment_elapsed_offset": offsets[seg - 1],
                    "wall_time_seconds": round(wall_time, 2),
                    "segments": segments,
                }

                out_path = _diarization_path(session_dir, diarize_channel, seg)
                out_path.write_text(json.dumps(sidecar, indent=2), encoding="utf-8")
                _progress(
                    f"  {len(segments)} segments, {wall_time:.1f}s ({device_used})"
                )

    except Exception:
        log.exception("Diarization failed; cleaning up partial output")
        _cleanup_diarization_files(session_dir)
        return False
    finally:
        # Free pipeline and MPS memory before summarizer loads MLX
        del pipeline
        try:
            import torch

            if hasattr(torch.mps, "empty_cache"):
                torch.mps.empty_cache()
        except Exception:
            pass

    _progress("Diarization complete.")
    return True


# ---------------------------------------------------------------------------
# Transcript labeling (consumption side)
# ---------------------------------------------------------------------------


def _load_diarization(session_dir: Path, segment: int) -> dict | None:
    """Load diarization sidecar JSON for a segment. Tries sys then mic."""
    for channel in ("sys", "mic"):
        path = _diarization_path(session_dir, channel, segment)
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                log.warning("Failed to read diarization file: %s", path)
    return None


def _find_speaker_at(audio_pos: float, segments: list[dict]) -> str | None:
    """Find the speaker label for a given audio position (seconds)."""
    for seg in segments:
        if seg["start"] <= audio_pos <= seg["end"]:
            return seg["speaker"]
    # If not in a segment, find the nearest one within 5 seconds
    best_dist = 5.0
    best_speaker = None
    for seg in segments:
        mid = (seg["start"] + seg["end"]) / 2
        dist = abs(audio_pos - mid)
        if dist < best_dist:
            best_dist = dist
            best_speaker = seg["speaker"]
    return best_speaker


def _map_speaker_label(
    raw_label: str,
    speaker_names: list[str],
    all_labels: list[str],
) -> str:
    """Map a pyannote speaker label (SPEAKER_00) to a display name.

    If speaker_names are provided and match the count, map by order.
    Otherwise use generic labels (Speaker A, Speaker B, etc.).
    """
    if not all_labels:
        return raw_label

    # Sort labels to get stable ordering (SPEAKER_00, SPEAKER_01, ...)
    sorted_labels = sorted(set(all_labels))
    try:
        idx = sorted_labels.index(raw_label)
    except ValueError:
        idx = -1

    if speaker_names and len(speaker_names) == len(sorted_labels) and idx >= 0:
        return speaker_names[idx]

    # Generic labels: Speaker A, Speaker B, ...
    if idx >= 0:
        letter = chr(ord("A") + idx) if idx < 26 else str(idx)
        return f"Speaker {letter}"

    return raw_label


def label_events(
    events: list[dict],
    session_dir: Path,
    segment: int,
    segment_elapsed_offset: int,
) -> list[dict]:
    """Return events with 'speaker' field added to transcript entries.

    Reads .diarization.json for the segment.  For each transcript event:
    - mic source + mic_speaker known → speaker = mic_speaker name
    - sys source + diarization data → speaker = mapped name
    - no diarization → speaker field not added (unchanged)
    """
    diar = _load_diarization(session_dir, segment)
    if diar is None:
        return events

    diar_segments = diar.get("segments", [])
    diar_channel = diar.get("channel", "sys")
    speaker_names = diar.get("speaker_names", [])
    mic_speaker = diar.get("mic_speaker")
    offset = diar.get("segment_elapsed_offset", segment_elapsed_offset)

    # Collect all unique speaker labels for stable ordering
    all_labels = list({seg["speaker"] for seg in diar_segments})

    labeled: list[dict] = []
    for event in events:
        event = dict(event)  # shallow copy

        if event.get("type") != "transcript":
            labeled.append(event)
            continue

        source = event.get("source", "mic")
        elapsed = event.get("elapsed", 0)

        if source == diar_channel:
            # This is the diarized channel — look up speaker
            audio_pos = elapsed - offset
            raw_label = _find_speaker_at(audio_pos, diar_segments)
            if raw_label:
                event["speaker"] = _map_speaker_label(
                    raw_label, speaker_names, all_labels
                )
        elif mic_speaker and source == "mic":
            # Mic source with known mic speaker name
            event["speaker"] = mic_speaker

        labeled.append(event)

    return labeled
