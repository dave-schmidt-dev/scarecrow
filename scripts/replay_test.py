#!/usr/bin/env python3
"""Replay recorded audio through Scarecrow's pipeline for automated testing.

Feeds a WAV file chunk-by-chunk through the real AudioRecorder or
SystemAudioCapture callback, exercises VAD silence detection, and
optionally transcribes via Parakeet. Results are printed or compared
against a saved baseline.

Usage:
    # VAD-only (no transcription, fast):
    python scripts/replay_test.py recordings/standup.wav --vad-only

    # Full transcription (requires Parakeet model):
    python scripts/replay_test.py recordings/standup.wav

    # Record a baseline from a known-good run:
    python scripts/replay_test.py recordings/standup.wav --save-baseline

    # Compare against saved baseline:
    python scripts/replay_test.py recordings/standup.wav --check-baseline

    # Replay as system audio (stereo, sys thresholds):
    python scripts/replay_test.py recordings/meeting.wav --source sys
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import TypedDict
from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# Ensure the project root is on sys.path so `scarecrow` is importable whether
# the script is run as `python scripts/replay_test.py` or from any CWD.
# ---------------------------------------------------------------------------

_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# ---------------------------------------------------------------------------
# Import real soundfile NOW — before the mock is installed — so load_wav can
# use it directly without any sys.modules surgery at runtime.
# ---------------------------------------------------------------------------

try:
    import soundfile as _real_soundfile
except ImportError:
    _real_soundfile = None  # type: ignore[assignment]

import numpy as np  # noqa: E402

# ---------------------------------------------------------------------------
# Mock audio I/O before importing Scarecrow so we don't open real devices.
# soundfile must already be imported above first so the mock doesn't shadow it.
# ---------------------------------------------------------------------------

_mock_sd = MagicMock()
_mock_sf = MagicMock()
sys.modules["sounddevice"] = _mock_sd
sys.modules["soundfile"] = _mock_sf

from scarecrow.config import Config  # noqa: E402
from scarecrow.recorder import AudioRecorder  # noqa: E402
from scarecrow.sys_audio import SystemAudioCapture  # noqa: E402

# Optional Parakeet transcription
_transcriber_available = False
try:
    from scarecrow.transcriber import Transcriber

    _transcriber_available = True
except Exception:
    pass

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

CHUNK_SIZE = 1024  # samples — matches real PortAudio callback size


class SegmentResult(TypedDict):
    offset_s: float
    duration_s: float
    text: str
    words: int


class BaselineFile(TypedDict):
    source: str
    wav_file: str
    silence_threshold: float
    min_silence_ms: int
    segments: list[SegmentResult]
    total_words: int


# ---------------------------------------------------------------------------
# WAV loading
# ---------------------------------------------------------------------------


def load_wav(wav_path: Path, target_rate: int = 48000) -> tuple[np.ndarray, int]:
    """Load a WAV file as int16 mono, resampling to target_rate if needed.

    Returns (samples_int16, sample_rate).  Uses the real soundfile module
    reference captured at import time (before the mock was installed).
    """
    if _real_soundfile is None:
        sys.exit("soundfile is not installed. Run: uv sync")

    sf = _real_soundfile
    data, sr = sf.read(str(wav_path), dtype="float32", always_2d=True)

    # Downmix to mono
    mono = data.mean(axis=1) if data.shape[1] > 1 else data[:, 0]

    # Resample if needed
    if sr != target_rate:
        duration = len(mono) / sr
        new_length = int(duration * target_rate)
        old_times = np.linspace(0, duration, num=len(mono), endpoint=False)
        new_times = np.linspace(0, duration, num=new_length, endpoint=False)
        mono = np.interp(new_times, old_times, mono).astype(np.float32)
        sr = target_rate

    # Convert to int16 (matching real PortAudio output)
    samples_int16 = (mono * 32768.0).clip(-32768, 32767).astype(np.int16)
    return samples_int16, sr


def format_duration(seconds: float) -> str:
    """Format seconds as M:SS or H:MM:SS."""
    s = int(seconds)
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def format_offset(seconds: float) -> str:
    """Format a timestamp offset as M:SS."""
    s = int(seconds)
    m, s = divmod(s, 60)
    return f"{m}:{s:02d}"


# ---------------------------------------------------------------------------
# Recorder / capture factory
# ---------------------------------------------------------------------------


def _make_mic_recorder(cfg: Config) -> AudioRecorder:
    """Build an AudioRecorder with mocked I/O, manually set to recording state."""
    recorder = AudioRecorder(output_path=Path("/dev/null"), cfg=cfg)

    # Bypass start() — it opens real sounddevice/soundfile streams.
    # Manually replicate just the state changes needed for _callback_inner:
    recorder._recording = True
    recorder._paused = False
    # The write queue and buffer_lock are already initialised in __init__.
    # No writer thread needed — we're not writing to disk.
    return recorder


def _make_sys_capture(
    cfg: Config, sample_rate: int, channels: int
) -> SystemAudioCapture:
    """Build a SystemAudioCapture with mocked I/O at the given sample rate."""
    # SystemAudioCapture.__init__ calls sd.query_devices() — mock it.
    _mock_sd.query_devices.return_value = {
        "default_samplerate": float(sample_rate),
        "max_input_channels": channels,
    }
    capture = SystemAudioCapture(output_path=Path("/dev/null"), device=0)
    # Manually set recording state (same bypass as mic recorder)
    capture._recording = True
    capture._paused = False
    return capture


# ---------------------------------------------------------------------------
# Core replay loop
# ---------------------------------------------------------------------------


def replay(
    wav_path: Path,
    source: str,
    cfg: Config,
    vad_only: bool,
) -> list[SegmentResult]:
    """Feed WAV through the pipeline chunk by chunk.

    Returns a list of SegmentResult dicts, one per VAD drain.
    """
    # Determine VAD thresholds based on source
    if source == "sys":
        silence_threshold = cfg.SYS_VAD_SILENCE_THRESHOLD
        min_silence_ms = cfg.SYS_VAD_MIN_SILENCE_MS
        min_speech_ratio = cfg.SYS_VAD_MIN_SPEECH_RATIO
        min_buffer_seconds = 5.0
    else:
        silence_threshold = cfg.VAD_SILENCE_THRESHOLD
        min_silence_ms = cfg.VAD_MIN_SILENCE_MS
        min_speech_ratio = cfg.VAD_MIN_SPEECH_RATIO
        min_buffer_seconds = 0.5

    target_rate = cfg.RECORDING_SAMPLE_RATE
    samples, sr = load_wav(wav_path, target_rate=target_rate)
    total_seconds = len(samples) / sr

    print(f"Replay: {wav_path} ({format_duration(total_seconds)}, {sr}Hz, {'mono'})")
    print(
        f"Source: {source} | Threshold: {silence_threshold:.3f} | "
        f"Min silence: {min_silence_ms}ms"
    )
    print()

    # Build recorder / capture
    if source == "sys":
        # Detect channel count from the raw file (before downmix)
        try:
            info = _real_soundfile.info(str(wav_path))
            raw_channels = info.channels
        except Exception:
            raw_channels = 2
        recorder_obj = _make_sys_capture(cfg, sr, channels=raw_channels)
        drain_kwargs = dict(
            silence_threshold=silence_threshold,
            min_silence_ms=min_silence_ms,
            max_buffer_seconds=float(cfg.VAD_MAX_BUFFER_SECONDS),
            min_buffer_seconds=min_buffer_seconds,
        )
    else:
        recorder_obj = _make_mic_recorder(cfg)
        drain_kwargs = dict(
            silence_threshold=silence_threshold,
            min_silence_ms=min_silence_ms,
            max_buffer_seconds=float(cfg.VAD_MAX_BUFFER_SECONDS),
        )

    # Optional transcriber
    transcriber = None
    if not vad_only and _transcriber_available:
        print("Loading Parakeet model...", end=" ", flush=True)
        t0 = time.perf_counter()
        transcriber = Transcriber(cfg=cfg)
        transcriber.prepare()
        transcriber.preload_batch_model()
        print(f"ready ({time.perf_counter() - t0:.1f}s)\n")
    elif not vad_only and not _transcriber_available:
        print("Note: Parakeet not available — running VAD-only mode.\n")
        vad_only = True

    segments: list[SegmentResult] = []
    chunk_samples = CHUNK_SIZE

    # Replay loop — feed chunks, poll VAD after each
    sample_pos = 0
    feed_count = 0
    wall_start = time.monotonic()

    while sample_pos < len(samples):
        chunk = samples[sample_pos : sample_pos + chunk_samples]
        if len(chunk) == 0:
            break

        # Pad last chunk to expected size (PortAudio always delivers full frames)
        if len(chunk) < chunk_samples:
            chunk = np.pad(chunk, (0, chunk_samples - len(chunk)))

        # Reshape to (frames, channels) as PortAudio delivers
        indata = chunk.reshape(-1, 1)

        # Inject into the callback
        if source == "sys":
            recorder_obj._callback_inner(indata, None)
        else:
            recorder_obj._callback_inner(indata, chunk_samples, None, None)

        sample_pos += chunk_samples
        feed_count += 1

        # Current playback position in the virtual timeline
        playback_pos_s = sample_pos / sr

        # Poll VAD
        result = recorder_obj.drain_to_silence(**drain_kwargs)
        if result is not None:
            audio, chunk_energies = result
            if len(audio) == 0:
                continue

            # Speech-ratio gate (mic source only; sys has ratio=0.0 by default)
            if min_speech_ratio > 0.0 and chunk_energies:
                energy_floor = silence_threshold * 0.5
                speech_chunks = sum(1 for e in chunk_energies if e >= energy_floor)
                speech_ratio = speech_chunks / len(chunk_energies)
                if speech_ratio < min_speech_ratio:
                    continue

            seg_duration_s = len(audio) / cfg.SAMPLE_RATE
            # Offset is where the drain occurred in the original file timeline
            offset_s = playback_pos_s - seg_duration_s

            text = ""
            if not vad_only and transcriber is not None:
                text = (
                    transcriber.transcribe_batch(
                        audio,
                        batch_elapsed=int(offset_s),
                        source=source,
                        emit_callback=False,
                    )
                    or ""
                )

            word_count = len(text.split()) if text.strip() else 0

            seg: SegmentResult = {
                "offset_s": round(offset_s, 2),
                "duration_s": round(seg_duration_s, 2),
                "text": text,
                "words": word_count,
            }
            segments.append(seg)

            seg_idx = len(segments)
            offset_fmt = format_offset(offset_s)
            if vad_only:
                print(
                    f"Segment {seg_idx} @ {offset_fmt} "
                    f"({seg_duration_s:.1f}s audio): [VAD drain — no transcription]"
                )
            else:
                preview = text if text else "(empty)"
                print(
                    f"Segment {seg_idx} @ {offset_fmt} "
                    f'({seg_duration_s:.1f}s audio): "{preview}"'
                )

    # Final drain — pick up any trailing audio
    if source == "sys":
        final = recorder_obj.drain_buffer()
    else:
        final = recorder_obj.drain_buffer()

    if final is not None and len(final) > 0:
        seg_duration_s = len(final) / cfg.SAMPLE_RATE
        offset_s = total_seconds - seg_duration_s

        text = ""
        if not vad_only and transcriber is not None:
            text = (
                transcriber.transcribe_batch(
                    final,
                    batch_elapsed=int(offset_s),
                    source=source,
                    emit_callback=False,
                )
                or ""
            )

        word_count = len(text.split()) if text.strip() else 0
        seg = {
            "offset_s": round(offset_s, 2),
            "duration_s": round(seg_duration_s, 2),
            "text": text,
            "words": word_count,
        }
        segments.append(seg)

        seg_idx = len(segments)
        offset_fmt = format_offset(offset_s)
        if vad_only:
            print(
                f"Segment {seg_idx} @ {offset_fmt} "
                f"({seg_duration_s:.1f}s audio): [VAD drain — no transcription]"
            )
        else:
            preview = text if text else "(empty)"
            print(
                f"Segment {seg_idx} @ {offset_fmt} "
                f'({seg_duration_s:.1f}s audio): "{preview}"'
            )

    wall_elapsed = time.monotonic() - wall_start
    total_words = sum(s["words"] for s in segments)
    n = len(segments)
    avg_seg = sum(s["duration_s"] for s in segments) / n if n else 0.0

    print()
    print("Summary:")
    print(f"  Segments:     {n}")
    if not vad_only:
        print(f"  Total words:  {total_words}")
    print(f"  VAD drains:   {n}")
    print(f"  Avg segment:  {avg_seg:.1f}s")
    print(f"  Wall time:    {wall_elapsed:.1f}s")
    if not vad_only and total_seconds > 0:
        rtf = wall_elapsed / total_seconds
        print(f"  RTF:          {rtf:.3f}x")

    return segments


# ---------------------------------------------------------------------------
# Baseline save / compare
# ---------------------------------------------------------------------------


def baseline_path(wav_path: Path) -> Path:
    return wav_path.with_suffix(".baseline")


def save_baseline(
    wav_path: Path,
    source: str,
    cfg: Config,
    segments: list[SegmentResult],
) -> None:
    """Write results to a .baseline JSON file."""
    silence_threshold = (
        cfg.SYS_VAD_SILENCE_THRESHOLD if source == "sys" else cfg.VAD_SILENCE_THRESHOLD
    )
    min_silence_ms = (
        cfg.SYS_VAD_MIN_SILENCE_MS if source == "sys" else cfg.VAD_MIN_SILENCE_MS
    )

    baseline: BaselineFile = {
        "source": source,
        "wav_file": str(wav_path),
        "silence_threshold": silence_threshold,
        "min_silence_ms": min_silence_ms,
        "segments": segments,
        "total_words": sum(s["words"] for s in segments),
    }
    out = baseline_path(wav_path)
    out.write_text(json.dumps(baseline, indent=2))
    print(f"\nBaseline saved: {out}")


def _jaccard(a: str, b: str) -> float:
    """Word-level Jaccard similarity between two strings."""
    wa = set(a.lower().split())
    wb = set(b.lower().split())
    if not wa and not wb:
        return 1.0
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)


def check_baseline(
    wav_path: Path,
    segments: list[SegmentResult],
) -> bool:
    """Compare segments against a saved baseline. Returns True if pass."""
    bp = baseline_path(wav_path)
    if not bp.exists():
        print(
            f"\nNo baseline found at {bp}. Run --save-baseline first.",
            file=sys.stderr,
        )
        return False

    baseline: BaselineFile = json.loads(bp.read_text())
    expected = baseline["segments"]
    expected_words = baseline["total_words"]

    print(f"\nBaseline: {bp}")
    passed = True
    fail_reasons = []

    # Segment count must match exactly
    if len(segments) != len(expected):
        fail_reasons.append(
            f"  Segment count mismatch: got {len(segments)}, expected {len(expected)}"
        )
        passed = False

    # Total word count within 10%
    got_words = sum(s["words"] for s in segments)
    if expected_words > 0:
        word_diff = abs(got_words - expected_words) / expected_words
        if word_diff > 0.10:
            fail_reasons.append(
                f"  Word count out of tolerance: got {got_words}, "
                f"expected {expected_words} (diff {word_diff:.1%} > 10%)"
            )
            passed = False

    # Per-segment text similarity (only when both have text)
    n_compare = min(len(segments), len(expected))
    low_sim_segs = []
    for i in range(n_compare):
        got_text = segments[i].get("text", "")
        exp_text = expected[i].get("text", "")
        if not got_text and not exp_text:
            continue
        sim = _jaccard(got_text, exp_text)
        if sim < 0.8:
            low_sim_segs.append((i + 1, sim, got_text[:60], exp_text[:60]))

    if low_sim_segs:
        passed = False
        for seg_i, sim, got, exp in low_sim_segs:
            fail_reasons.append(
                f"  Segment {seg_i} Jaccard similarity {sim:.2f} < 0.80\n"
                f"    Got:      {got!r}\n"
                f"    Expected: {exp!r}"
            )

    # Print result
    if passed:
        print("PASS — results match baseline within tolerance.")
        print(f"  Segments: {len(segments)}/{len(expected)}")
        if expected_words > 0:
            print(f"  Words: {got_words}/{expected_words}")
    else:
        print("FAIL — baseline mismatch:")
        for r in fail_reasons:
            print(r)

    return passed


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Replay a WAV file through Scarecrow's audio pipeline.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("wav_file", type=Path, help="Path to input WAV file")
    p.add_argument(
        "--source",
        choices=["mic", "sys"],
        default="mic",
        help="Pipeline source: 'mic' (default) or 'sys' (system audio thresholds)",
    )
    p.add_argument(
        "--vad-only",
        action="store_true",
        help="Skip transcription — show VAD drain points only (fast)",
    )
    p.add_argument(
        "--save-baseline",
        action="store_true",
        help="Save results to {wav_file}.baseline for future comparison",
    )
    p.add_argument(
        "--check-baseline",
        action="store_true",
        help="Compare output against previously saved baseline",
    )
    p.add_argument(
        "--silence-threshold",
        type=float,
        default=None,
        metavar="FLOAT",
        help="Override VAD silence threshold (default: from config)",
    )
    p.add_argument(
        "--min-silence-ms",
        type=int,
        default=None,
        metavar="MS",
        help="Override VAD minimum silence duration in ms (default: from config)",
    )
    return p


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    wav_path: Path = args.wav_file.expanduser().resolve()
    if not wav_path.exists():
        print(f"Error: WAV file not found: {wav_path}", file=sys.stderr)
        return 1

    # Build config, applying any overrides
    cfg = Config()
    if args.silence_threshold is not None:
        if args.source == "sys":
            cfg.SYS_VAD_SILENCE_THRESHOLD = args.silence_threshold
        else:
            cfg.VAD_SILENCE_THRESHOLD = args.silence_threshold
    if args.min_silence_ms is not None:
        if args.source == "sys":
            cfg.SYS_VAD_MIN_SILENCE_MS = args.min_silence_ms
        else:
            cfg.VAD_MIN_SILENCE_MS = args.min_silence_ms

    # --check-baseline implies we need transcription (to compare text)
    # unless user also passed --vad-only (VAD-only baseline comparison
    # ignores text similarity and only checks segment count).
    vad_only = args.vad_only

    segments = replay(
        wav_path=wav_path,
        source=args.source,
        cfg=cfg,
        vad_only=vad_only,
    )

    if args.save_baseline:
        save_baseline(wav_path, args.source, cfg, segments)

    if args.check_baseline:
        passed = check_baseline(wav_path, segments)
        return 0 if passed else 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
