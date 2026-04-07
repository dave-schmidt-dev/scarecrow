#!/usr/bin/env python3
"""Evaluate pyannote-audio speaker diarization on existing scarecrow recordings.

Runs diarization on mic and/or sys audio files, reports per-speaker statistics,
flags degenerate outputs, and writes human-reviewable timeline markdown.

Each audio file is diarized independently -- speaker IDs are local to one run
and cannot be compared across files.

Usage:
    uv sync --extra diarization
    uv run python benchmarks/bench_diarization.py \\
        ~/recordings/2026-04-03_16-14-24_signal-call-wmike-and-justin \\
        --clip-seconds 300 --num-speakers 3

    # Compare models:
    uv run python benchmarks/bench_diarization.py SESSION --model community-1

Prerequisites:
    1. Accept HuggingFace licenses (both required):
       - https://huggingface.co/pyannote/speaker-diarization-3.1
       - https://huggingface.co/pyannote/segmentation-3.0
    2. Set HF_TOKEN env var with your HuggingFace token
    3. ffmpeg must be installed (required by torchcodec)
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import shutil
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import psutil
import soundfile as sf

# Model aliases -> HuggingFace pipeline IDs
MODEL_ALIASES = {
    "3.1": "pyannote/speaker-diarization-3.1",
    "community-1": "pyannote/speaker-diarization-community-1",
}

MIN_AUDIO_SECONDS = 5.0  # skip files shorter than this

OUTPUT_DIR = Path(__file__).parent / "diarization_eval"


# ---------------------------------------------------------------------------
# Resource monitor (pattern from bench_librispeech.py)
# ---------------------------------------------------------------------------


@dataclass
class ResourceSnapshot:
    timestamp: float
    rss_mb: float


@dataclass
class ResourceMonitor:
    """Background thread that samples RSS memory at intervals."""

    interval: float = 1.0
    snapshots: list[ResourceSnapshot] = field(default_factory=list)
    _stop: threading.Event = field(default_factory=threading.Event)
    _thread: threading.Thread | None = None
    _process: psutil.Process = field(default_factory=lambda: psutil.Process())

    def start(self) -> None:
        self.snapshots.clear()
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> list[ResourceSnapshot]:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
        return self.snapshots

    def _run(self) -> None:
        while not self._stop.is_set():
            snap = ResourceSnapshot(
                timestamp=time.perf_counter(),
                rss_mb=self._process.memory_info().rss / (1024 * 1024),
            )
            self.snapshots.append(snap)
            self._stop.wait(self.interval)


def peak_rss(snapshots: list[ResourceSnapshot]) -> float:
    return max((s.rss_mb for s in snapshots), default=0.0)


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class AudioFile:
    path: Path
    channel: str  # "mic" or "sys"
    segment: int  # 1-based
    duration_s: float
    sample_rate: int
    channels: int


@dataclass
class SpeakerSegment:
    start: float
    end: float
    speaker: str

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass
class SpeakerStats:
    speaker: str
    speech_time_s: float
    segment_count: int
    avg_duration_s: float
    min_duration_s: float
    max_duration_s: float


@dataclass
class DiarResult:
    audio_file: AudioFile
    segments: list[SpeakerSegment]
    speaker_count: int
    speaker_stats: list[SpeakerStats]
    total_speech_s: float
    coverage_pct: float  # % of audio with a speaker assigned
    wall_time_s: float
    rss_peak_mb: float
    flags: list[str]


@dataclass
class TranscriptEntry:
    elapsed: int
    text: str
    source: str | None  # None for pre-dual-channel sessions


# ---------------------------------------------------------------------------
# Session discovery
# ---------------------------------------------------------------------------


def discover_audio_files(session_dir: Path, channels: str) -> list[AudioFile]:
    """Find mic and/or sys audio files in a session directory."""
    files: list[AudioFile] = []

    # Patterns: audio.flac, audio_seg2.flac, audio.wav, etc.
    for suffix in (".flac", ".wav"):
        # Mic files
        if channels in ("mic", "both"):
            mic_main = session_dir / f"audio{suffix}"
            if mic_main.exists():
                files.append(_make_audio_file(mic_main, "mic", 1))
            # Segments: audio_seg2.flac, audio_seg3.flac, ...
            for seg_file in sorted(session_dir.glob(f"audio_seg*{suffix}")):
                seg_num = _parse_segment_number(seg_file.stem)
                if seg_num:
                    files.append(_make_audio_file(seg_file, "mic", seg_num))

        # Sys files
        if channels in ("sys", "both"):
            sys_main = session_dir / f"audio_sys{suffix}"
            if sys_main.exists():
                files.append(_make_audio_file(sys_main, "sys", 1))
            for seg_file in sorted(session_dir.glob(f"audio_sys_seg*{suffix}")):
                seg_num = _parse_segment_number(seg_file.stem.replace("audio_sys_", ""))
                if seg_num:
                    files.append(_make_audio_file(seg_file, "sys", seg_num))

    # Deduplicate (prefer FLAC over WAV)
    seen: dict[tuple[str, int], AudioFile] = {}
    for af in files:
        key = (af.channel, af.segment)
        if key not in seen or af.path.suffix == ".flac":
            seen[key] = af
    return sorted(seen.values(), key=lambda f: (f.channel, f.segment))


def _make_audio_file(path: Path, channel: str, segment: int) -> AudioFile:
    info = sf.info(str(path))
    return AudioFile(
        path=path,
        channel=channel,
        segment=segment,
        duration_s=info.duration,
        sample_rate=info.samplerate,
        channels=info.channels,
    )


def _parse_segment_number(stem: str) -> int | None:
    """Extract segment number from 'seg2', 'seg3', etc."""
    import re

    m = re.search(r"seg(\d+)", stem)
    return int(m.group(1)) if m else None


def load_transcript(session_dir: Path) -> list[TranscriptEntry]:
    """Load transcript entries from session JSONL."""
    jsonl = session_dir / "transcript.jsonl"
    if not jsonl.exists():
        return []
    entries = []
    with open(jsonl) as f:
        for line in f:
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("type") != "transcript":
                continue
            entries.append(
                TranscriptEntry(
                    elapsed=obj.get("elapsed", 0),
                    text=obj.get("text", ""),
                    source=obj.get("source"),
                )
            )
    return entries


# ---------------------------------------------------------------------------
# Audio preprocessing
# ---------------------------------------------------------------------------


def prepare_audio(
    audio_file: AudioFile, clip_seconds: float | None, tmp_dir: Path
) -> Path:
    """Return a path suitable for pyannote: mono WAV, optionally clipped.

    If the file is already mono and no clipping is needed, returns the
    original path.  Otherwise writes a temp WAV to *tmp_dir*.
    """
    needs_conversion = audio_file.channels > 1
    needs_clip = clip_seconds is not None and clip_seconds < audio_file.duration_s

    if not needs_conversion and not needs_clip:
        return audio_file.path

    # Read audio
    data, sr = sf.read(str(audio_file.path), dtype="float32", always_2d=True)

    # Downmix stereo to mono
    if data.shape[1] > 1:
        data = data.mean(axis=1, keepdims=True)

    # Clip
    if needs_clip:
        max_frames = int(clip_seconds * sr)
        data = data[:max_frames]

    # Write temp mono WAV
    stem = f"{audio_file.channel}_seg{audio_file.segment}"
    tmp_path = tmp_dir / f"{stem}.wav"
    sf.write(str(tmp_path), data, sr, subtype="FLOAT")
    return tmp_path


# ---------------------------------------------------------------------------
# Diarization
# ---------------------------------------------------------------------------


def load_pipeline(model_id: str, device: str, hf_token: str):
    """Load a pyannote diarization pipeline."""
    from pyannote.audio import Pipeline

    print(f"  Loading pipeline: {model_id} (device={device})")
    t0 = time.perf_counter()
    pipeline = Pipeline.from_pretrained(model_id, token=hf_token)
    load_time = time.perf_counter() - t0
    print(f"  Pipeline loaded in {load_time:.1f}s")

    if device != "cpu":
        import torch

        pipeline.to(torch.device(device))
        print(f"  Moved to {device}")

    return pipeline, load_time


def run_diarization(
    pipeline,
    audio_path: Path,
    audio_file: AudioFile,
    num_speakers: int | None,
    effective_duration: float | None = None,
) -> DiarResult:
    """Run diarization on one audio file."""
    monitor = ResourceMonitor(interval=0.5)
    monitor.start()

    kwargs = {}
    if num_speakers is not None:
        kwargs["num_speakers"] = num_speakers

    # Progress hook — prints each pipeline step as it completes
    def _progress_hook(step_name, step_artefact, file, **kw):
        elapsed = time.perf_counter() - t0
        print(f"    [{elapsed:6.1f}s] {step_name}", flush=True)

    kwargs["hook"] = _progress_hook

    t0 = time.perf_counter()
    output = pipeline(str(audio_path), **kwargs)
    wall_time = time.perf_counter() - t0

    snapshots = monitor.stop()
    rss = peak_rss(snapshots)

    # pyannote 4.0 returns DiarizeOutput; extract the Annotation
    annotation = getattr(output, "speaker_diarization", output)

    # Extract segments
    segments: list[SpeakerSegment] = []
    for turn, _, speaker in annotation.itertracks(yield_label=True):
        segments.append(SpeakerSegment(start=turn.start, end=turn.end, speaker=speaker))

    # Compute per-speaker stats
    speakers: dict[str, list[SpeakerSegment]] = {}
    for seg in segments:
        speakers.setdefault(seg.speaker, []).append(seg)

    stats: list[SpeakerStats] = []
    for spk, segs in sorted(speakers.items()):
        durations = [s.duration for s in segs]
        stats.append(
            SpeakerStats(
                speaker=spk,
                speech_time_s=sum(durations),
                segment_count=len(segs),
                avg_duration_s=sum(durations) / len(durations),
                min_duration_s=min(durations),
                max_duration_s=max(durations),
            )
        )

    total_speech = sum(s.speech_time_s for s in stats)
    duration = effective_duration or audio_file.duration_s
    coverage = (total_speech / duration * 100) if duration > 0 else 0.0

    # Degenerate flags
    flags = _compute_flags(stats, num_speakers)

    return DiarResult(
        audio_file=audio_file,
        segments=segments,
        speaker_count=len(speakers),
        speaker_stats=stats,
        total_speech_s=total_speech,
        coverage_pct=min(coverage, 100.0),  # can exceed 100% with overlap
        wall_time_s=wall_time,
        rss_peak_mb=rss,
        flags=flags,
    )


def _compute_flags(
    stats: list[SpeakerStats], expected_speakers: int | None
) -> list[str]:
    flags = []
    if not stats:
        flags.append("NO_SPEECH: no speakers detected")
        return flags

    total = sum(s.speech_time_s for s in stats)
    if total > 0:
        dominant_pct = max(s.speech_time_s for s in stats) / total * 100
        if dominant_pct > 90 and len(stats) > 1:
            flags.append(f"DOMINANT: one speaker has {dominant_pct:.0f}% of speech")

    if expected_speakers is not None and len(stats) != expected_speakers:
        flags.append(
            f"COUNT_MISMATCH: detected {len(stats)}, expected {expected_speakers}"
        )

    if len(stats) == 1 and expected_speakers is not None and expected_speakers > 1:
        flags.append("SINGLE_SPEAKER: only 1 speaker on multi-speaker audio")

    # Over-fragmentation: median segment < 1s
    all_durations = []
    for s in stats:
        all_durations.extend(
            [s.min_duration_s] * 1
        )  # approximate; use per-segment data
    # Better: check avg across all speakers
    all_seg_counts = sum(s.segment_count for s in stats)
    if total > 0 and all_seg_counts > 0:
        median_approx = total / all_seg_counts
        if median_approx < 1.0:
            flags.append(f"FRAGMENTED: avg segment {median_approx:.1f}s (< 1s)")

    return flags


# ---------------------------------------------------------------------------
# Timeline output
# ---------------------------------------------------------------------------


def _fmt_time(seconds: float) -> str:
    """Format seconds as M:SS or H:MM:SS."""
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _fmt_duration(seconds: float) -> str:
    """Format duration as human-readable string."""
    if seconds < 60:
        return f"{seconds:.0f}s"
    m = seconds / 60
    if m < 60:
        return f"{m:.0f}m {seconds % 60:.0f}s"
    h = m / 60
    return f"{h:.0f}h {m % 60:.0f}m"


def find_nearest_transcript(
    timestamp: float,
    transcript: list[TranscriptEntry],
    source_filter: str | None,
    tolerance: float = 10.0,
) -> str | None:
    """Find the transcript entry closest to a given timestamp."""
    best_text = None
    best_dist = tolerance
    for entry in transcript:
        # Filter by source if available
        if source_filter and entry.source and entry.source != source_filter:
            continue
        dist = abs(entry.elapsed - timestamp)
        if dist < best_dist:
            best_dist = dist
            best_text = entry.text
    return best_text


def write_session_report(
    session_dir: Path,
    results: list[DiarResult],
    transcript: list[TranscriptEntry],
    model_id: str,
    device: str,
    output_dir: Path,
) -> Path:
    """Write per-session diarization report to benchmarks/diarization_eval/."""
    slug = session_dir.name
    out_path = output_dir / f"{slug}.md"

    lines: list[str] = []
    lines.append(f"# Diarization: {slug}")
    lines.append("")
    lines.append(f"Model: `{model_id}` | Device: {device}")
    lines.append("")

    for result in results:
        af = result.audio_file
        ch_label = af.channel.upper()
        seg_label = f" seg{af.segment}" if af.segment > 1 else ""
        lines.append(
            f"## {ch_label}{seg_label} -- {af.path.name} "
            f"({_fmt_duration(af.duration_s)})"
        )
        lines.append("")

        # Speaker summary table
        lines.append("| Speaker | Speech | Segments | Avg | Min | Max |")
        lines.append("|---------|--------|----------|-----|-----|-----|")
        for s in result.speaker_stats:
            lines.append(
                f"| {s.speaker} | {_fmt_duration(s.speech_time_s)} "
                f"| {s.segment_count} | {s.avg_duration_s:.1f}s "
                f"| {s.min_duration_s:.1f}s | {s.max_duration_s:.1f}s |"
            )
        lines.append("")

        lines.append(
            f"Speakers: {result.speaker_count} | "
            f"Coverage: {result.coverage_pct:.0f}% | "
            f"RTF: {result.wall_time_s / af.duration_s:.2f}x | "
            f"Wall: {_fmt_duration(result.wall_time_s)} | "
            f"RSS peak: {result.rss_peak_mb:.0f} MB"
        )
        if result.flags:
            lines.append(f"**Flags:** {', '.join(result.flags)}")
        else:
            lines.append("Flags: none")
        lines.append("")

        # Timeline (first 80 segments)
        source_filter = af.channel if transcript else None
        lines.append("### Timeline")
        lines.append("")
        lines.append("| Time | Speaker | Nearest transcript |")
        lines.append("|------|---------|-------------------|")
        for seg in result.segments[:80]:
            nearest = find_nearest_transcript(seg.start, transcript, source_filter)
            if nearest and len(nearest) > 60:
                text_col = nearest[:60] + "..."
            else:
                text_col = nearest or ""
            lines.append(
                f"| {_fmt_time(seg.start)}--{_fmt_time(seg.end)} "
                f"| {seg.speaker} | {text_col} |"
            )
        if len(result.segments) > 80:
            lines.append(f"| ... | ({len(result.segments) - 80} more) | |")
        lines.append("")

    out_path.write_text("\n".join(lines))
    return out_path


def write_summary_report(
    all_results: list[tuple[Path, list[DiarResult]]],
    model_id: str,
    device: str,
    output_dir: Path,
) -> Path:
    """Write aggregate summary across all sessions."""
    now = datetime.datetime.now().strftime("%Y-%m-%d")
    model_tag = model_id.split("/")[-1]
    out_path = output_dir / f"summary_{model_tag}_{now}.md"

    lines: list[str] = []
    lines.append("# Diarization Evaluation Summary")
    lines.append("")
    lines.append(f"Date: {now} | Model: `{model_id}` | Device: {device}")
    lines.append("")

    lines.append(
        "| Session | Channel | Seg | Duration | Speakers | Coverage "
        "| RTF | Wall | RSS MB | Flags |"
    )
    lines.append(
        "|---------|---------|-----|----------|----------|----------"
        "|-----|------|--------|-------|"
    )

    total_audio = 0.0
    total_wall = 0.0

    for session_dir, results in all_results:
        slug = session_dir.name
        # Shorten slug for table readability
        short = slug[20:] if len(slug) > 20 else slug
        for r in results:
            af = r.audio_file
            seg_str = str(af.segment) if af.segment > 1 else ""
            flag_str = ", ".join(r.flags) if r.flags else ""
            rtf = r.wall_time_s / af.duration_s if af.duration_s > 0 else 0
            lines.append(
                f"| {short} | {af.channel} | {seg_str} "
                f"| {_fmt_duration(af.duration_s)} | {r.speaker_count} "
                f"| {r.coverage_pct:.0f}% | {rtf:.2f}x "
                f"| {_fmt_duration(r.wall_time_s)} | {r.rss_peak_mb:.0f} "
                f"| {flag_str} |"
            )
            total_audio += af.duration_s
            total_wall += r.wall_time_s

    lines.append("")
    lines.append(
        f"**Total audio:** {_fmt_duration(total_audio)} | "
        f"**Total wall time:** {_fmt_duration(total_wall)} | "
        f"**Overall RTF:** {total_wall / total_audio:.2f}x"
        if total_audio > 0
        else "No audio processed."
    )
    lines.append("")

    out_path.write_text("\n".join(lines))
    return out_path


# ---------------------------------------------------------------------------
# Preflight checks
# ---------------------------------------------------------------------------


def preflight_checks() -> str | None:
    """Check prerequisites. Returns HF token or exits with error."""
    # HF token
    hf_token = os.environ.get("HF_TOKEN")
    if not hf_token:
        print("ERROR: HF_TOKEN environment variable not set.")
        print("")
        print("Set your HuggingFace token:")
        print("  export HF_TOKEN=hf_...")
        print("")
        print("You must also accept the model licenses:")
        print("  https://huggingface.co/pyannote/speaker-diarization-3.1")
        print("  https://huggingface.co/pyannote/segmentation-3.0")
        sys.exit(1)

    # ffmpeg
    if not shutil.which("ffmpeg"):
        print("ERROR: ffmpeg not found. Required by pyannote-audio (torchcodec).")
        print("  brew install ffmpeg")
        sys.exit(1)

    # pyannote
    try:
        import pyannote.audio  # noqa: F401
    except ImportError:
        print("ERROR: pyannote-audio not installed.")
        print("  uv sync --extra diarization")
        sys.exit(1)

    return hf_token


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Evaluate pyannote speaker diarization on scarecrow recordings",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "sessions",
        nargs="+",
        type=Path,
        help="Session directories to evaluate",
    )
    parser.add_argument(
        "--clip-seconds",
        type=float,
        default=None,
        help="Process only the first N seconds of each file",
    )
    parser.add_argument(
        "--num-speakers",
        type=int,
        default=None,
        help="Expected number of speakers (hint to pyannote)",
    )
    parser.add_argument(
        "--channels",
        choices=["mic", "sys", "both"],
        default="both",
        help="Which audio channels to evaluate (default: both)",
    )
    parser.add_argument(
        "--device",
        choices=["cpu", "mps"],
        default="cpu",
        help="Torch device (default: cpu; mps has known accuracy bugs)",
    )
    parser.add_argument(
        "--model",
        default="3.1",
        choices=list(MODEL_ALIASES.keys()),
        help="Diarization model (default: 3.1)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help=f"Output directory (default: {OUTPUT_DIR})",
    )
    args = parser.parse_args()

    hf_token = preflight_checks()
    model_id = MODEL_ALIASES[args.model]
    output_dir = args.output_dir or OUTPUT_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    # Discover all audio files
    all_files: list[tuple[Path, list[AudioFile]]] = []
    total_audio_s = 0.0

    for session_dir in args.sessions:
        session_dir = session_dir.expanduser().resolve()
        if not session_dir.is_dir():
            print(f"WARNING: {session_dir} is not a directory, skipping")
            continue
        files = discover_audio_files(session_dir, args.channels)
        # Filter by minimum duration
        files = [f for f in files if f.duration_s >= MIN_AUDIO_SECONDS]
        if not files:
            print(f"WARNING: no audio files found in {session_dir.name}")
            continue
        all_files.append((session_dir, files))
        for f in files:
            dur = min(f.duration_s, args.clip_seconds or f.duration_s)
            total_audio_s += dur

    if not all_files:
        print("ERROR: no valid sessions found")
        sys.exit(1)

    # Estimate runtime
    n_files = sum(len(files) for _, files in all_files)
    rtf_estimate = 2.0 if args.device == "cpu" else 0.5
    est_minutes = total_audio_s * rtf_estimate / 60
    print(f"\n{'=' * 60}")
    print("  Diarization Evaluation")
    print(f"  Model:    {model_id}")
    print(f"  Device:   {args.device}")
    print(f"  Sessions: {len(all_files)}")
    print(f"  Files:    {n_files}")
    print(f"  Audio:    {_fmt_duration(total_audio_s)}")
    print(f"  Est time: ~{_fmt_duration(est_minutes * 60)}")
    if args.clip_seconds:
        print(f"  Clip:     first {args.clip_seconds:.0f}s per file")
    print(f"{'=' * 60}\n")

    # Load pipeline once
    pipeline, load_time = load_pipeline(model_id, args.device, hf_token)

    # Process sessions
    all_results: list[tuple[Path, list[DiarResult]]] = []

    with tempfile.TemporaryDirectory(prefix="scarecrow_diar_") as tmp_dir:
        tmp_path = Path(tmp_dir)

        for session_dir, files in all_files:
            print(f"\n--- {session_dir.name} ---")
            transcript = load_transcript(session_dir)
            session_results: list[DiarResult] = []

            for af in files:
                print(
                    f"  {af.channel} seg{af.segment}: "
                    f"{af.path.name} ({_fmt_duration(af.duration_s)}, "
                    f"{af.sample_rate}Hz, {af.channels}ch)"
                )

                audio_path = prepare_audio(af, args.clip_seconds, tmp_path)
                eff_dur = (
                    min(af.duration_s, args.clip_seconds)
                    if args.clip_seconds
                    else af.duration_s
                )
                result = run_diarization(
                    pipeline, audio_path, af, args.num_speakers, eff_dur
                )
                session_results.append(result)

                # Print summary
                rtf = result.wall_time_s / eff_dur
                print(
                    f"    -> {result.speaker_count} speakers, "
                    f"{result.coverage_pct:.0f}% coverage, "
                    f"RTF {rtf:.2f}x, {result.wall_time_s:.1f}s wall"
                )
                for flag in result.flags:
                    print(f"    ** {flag}")

            # Write per-session report
            report = write_session_report(
                session_dir,
                session_results,
                transcript,
                model_id,
                args.device,
                output_dir,
            )
            print(f"  -> {report}")
            all_results.append((session_dir, session_results))

    # Write aggregate summary
    summary = write_summary_report(all_results, model_id, args.device, output_dir)
    print(f"\nSummary: {summary}")

    # Final stats
    total_wall = sum(r.wall_time_s for _, results in all_results for r in results)
    print(f"\nPipeline load: {load_time:.1f}s")
    print(f"Total diarization: {_fmt_duration(total_wall)}")
    if total_audio_s > 0:
        print(f"Overall RTF: {total_wall / total_audio_s:.2f}x")
    print("Done.")


if __name__ == "__main__":
    main()
