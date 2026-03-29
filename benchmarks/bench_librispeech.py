#!/usr/bin/env python3
"""Benchmark parakeet-mlx on LibriSpeech test-clean.

Concatenates utterances into long audio streams (default ~3 min each),
then transcribes in batch chunks to simulate real scarecrow usage. Tracks
speed, accuracy, CPU, memory, and GPU memory throughout.

Supports both VAD-based chunking (matching scarecrow's live behaviour) and
fixed-interval chunking for controlled comparison.

Usage:
    uv run python benchmarks/bench_librispeech.py [--minutes N] [--vad]

VAD parameter tuning:
    uv run python benchmarks/bench_librispeech.py --vad --min-silence-ms 300
    uv run python benchmarks/bench_librispeech.py --vad --min-silence-ms 600
    uv run python benchmarks/bench_librispeech.py --sweep   # compare configurations
"""

from __future__ import annotations

import argparse
import gc
import random
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import psutil
import soundfile as sf

LIBRISPEECH_ROOT = Path(__file__).parent / "data" / "LibriSpeech" / "test-clean"

DEFAULT_CHUNK_SECONDS = 15

KNOWN_MODELS = [
    "mlx-community/parakeet-tdt-0.6b-v3",
    "mlx-community/parakeet-tdt-1.1b",
    "mlx-community/parakeet-rnnt-0.6b-v2",
    "mlx-community/parakeet-rnnt-1.1b-v2",
    "mlx-community/parakeet-ctc-0.6b-v2",
    "mlx-community/parakeet-ctc-1.1b-v2",
]

# VAD parameters matching config.py defaults
VAD_SILENCE_THRESHOLD = 0.01
VAD_MIN_SILENCE_MS = 600
VAD_MAX_BUFFER_SECONDS = 30


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------


def load_all_utterances(seed: int = 42) -> list[dict]:
    """Load all utterances with paths + ground truth."""
    entries: list[dict] = []
    for trans_file in LIBRISPEECH_ROOT.rglob("*.trans.txt"):
        chapter_dir = trans_file.parent
        with open(trans_file) as f:
            for line in f:
                parts = line.strip().split(" ", 1)
                if len(parts) != 2:
                    continue
                utt_id, text = parts
                flac_path = chapter_dir / f"{utt_id}.flac"
                if flac_path.exists():
                    entries.append({"id": utt_id, "path": flac_path, "reference": text})
    random.seed(seed)
    random.shuffle(entries)
    return entries


def build_stream(
    utterances: list[dict], target_seconds: float
) -> tuple[np.ndarray, str]:
    """Concatenate utterances into one long audio stream. Return (audio, reference)."""
    chunks = []
    refs = []
    total = 0.0
    for utt in utterances:
        audio, sr = sf.read(utt["path"], dtype="float32")
        assert sr == 16000
        dur = len(audio) / 16000
        chunks.append(audio)
        # Half-second silence between utterances
        chunks.append(np.zeros(8000, dtype=np.float32))
        refs.append(utt["reference"])
        total += dur + 0.5
        if total >= target_seconds:
            break
    return np.concatenate(chunks), " ".join(refs)


# ---------------------------------------------------------------------------
# WER
# ---------------------------------------------------------------------------


def _normalize(text: str) -> list[str]:
    """Lowercase, strip punctuation, split into words."""
    import re

    text = text.lower()
    text = re.sub(r"[^\w\s']", "", text)  # keep apostrophes for contractions
    return text.split()


def word_error_rate(ref: str, hyp: str) -> float:
    """Word error rate via Levenshtein distance (normalized)."""
    r_words = _normalize(ref)
    h_words = _normalize(hyp)
    r, h = len(r_words), len(h_words)
    d = [[0] * (h + 1) for _ in range(r + 1)]
    for i in range(r + 1):
        d[i][0] = i
    for j in range(h + 1):
        d[0][j] = j
    for i in range(1, r + 1):
        for j in range(1, h + 1):
            if r_words[i - 1] == h_words[j - 1]:
                d[i][j] = d[i - 1][j - 1]
            else:
                d[i][j] = 1 + min(d[i - 1][j], d[i][j - 1], d[i - 1][j - 1])
    return d[r][h] / max(r, 1)


# ---------------------------------------------------------------------------
# Resource monitor
# ---------------------------------------------------------------------------


@dataclass
class ResourceSnapshot:
    timestamp: float
    cpu_percent: float
    rss_mb: float
    gpu_active_mb: float | None = None
    gpu_peak_mb: float | None = None


@dataclass
class ResourceMonitor:
    """Background thread that samples CPU, memory, and GPU at intervals."""

    interval: float = 0.5
    snapshots: list[ResourceSnapshot] = field(default_factory=list)
    _stop: threading.Event = field(default_factory=threading.Event)
    _thread: threading.Thread | None = None
    _process: psutil.Process = field(default_factory=lambda: psutil.Process())

    def start(self):
        self._process.cpu_percent()  # prime the counter
        self.snapshots.clear()
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> list[ResourceSnapshot]:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
        return self.snapshots

    def _run(self):
        while not self._stop.is_set():
            snap = ResourceSnapshot(
                timestamp=time.perf_counter(),
                cpu_percent=self._process.cpu_percent(),
                rss_mb=self._process.memory_info().rss / (1024 * 1024),
            )
            try:
                import mlx.core as mx

                snap.gpu_active_mb = mx.metal.get_active_memory() / (1024 * 1024)
                snap.gpu_peak_mb = mx.metal.get_peak_memory() / (1024 * 1024)
            except Exception:
                pass
            self.snapshots.append(snap)
            self._stop.wait(self.interval)


def summarize_resources(snapshots: list[ResourceSnapshot]) -> dict:
    """Aggregate resource snapshots into summary stats."""
    if not snapshots:
        return {}
    cpus = [s.cpu_percent for s in snapshots]
    rss = [s.rss_mb for s in snapshots]
    gpu = [s.gpu_active_mb for s in snapshots if s.gpu_active_mb is not None]
    gpu_peak = [s.gpu_peak_mb for s in snapshots if s.gpu_peak_mb is not None]
    result = {
        "cpu_mean": sum(cpus) / len(cpus),
        "cpu_max": max(cpus),
        "rss_mean_mb": sum(rss) / len(rss),
        "rss_max_mb": max(rss),
    }
    if gpu:
        result["gpu_active_mean_mb"] = sum(gpu) / len(gpu)
        result["gpu_active_max_mb"] = max(gpu)
    if gpu_peak:
        result["gpu_peak_mb"] = max(gpu_peak)
    return result


# ---------------------------------------------------------------------------
# Backend
# ---------------------------------------------------------------------------


def make_parakeet(model_id: str = "mlx-community/parakeet-tdt-1.1b"):
    from parakeet_mlx import from_pretrained

    return from_pretrained(model_id)


def transcribe_parakeet(model, audio: np.ndarray) -> str:
    import mlx.core as mx
    from parakeet_mlx.audio import get_logmel

    audio_mx = mx.array(audio)
    mel = get_logmel(audio_mx, model.preprocessor_config)
    result = model.generate(mel)[0]
    return result.text.strip() if result.text else ""


# ---------------------------------------------------------------------------
# Benchmark runner
# ---------------------------------------------------------------------------


@dataclass
class ChunkResult:
    chunk_idx: int
    audio_duration: float
    wall_seconds: float
    rtf: float
    hypothesis: str


@dataclass
class BenchmarkResult:
    backend: str
    total_audio_s: float
    total_wall_s: float
    rtf: float
    wer: float
    chunk_results: list[ChunkResult]
    resources: dict


@dataclass
class VADConfig:
    """VAD parameters for benchmarking. Defaults match config.py."""

    silence_threshold: float = VAD_SILENCE_THRESHOLD
    min_silence_ms: int = VAD_MIN_SILENCE_MS
    max_buffer_seconds: int = VAD_MAX_BUFFER_SECONDS

    @property
    def label(self) -> str:
        return (
            f"thresh={self.silence_threshold} "
            f"silence={self.min_silence_ms}ms "
            f"max={self.max_buffer_seconds}s"
        )


def vad_split(
    audio: np.ndarray,
    sample_rate: int = 16000,
    vad: VADConfig | None = None,
) -> list[np.ndarray]:
    """Split audio at silence boundaries, mimicking recorder VAD logic."""
    if vad is None:
        vad = VADConfig()
    block_size = 512  # ~32ms at 16kHz, typical PortAudio callback size
    n_blocks = len(audio) // block_size

    # Compute per-block RMS
    energies = []
    for i in range(n_blocks):
        block = audio[i * block_size : (i + 1) * block_size]
        rms = float(np.sqrt(np.mean(block**2)))
        energies.append(rms)

    min_silence_blocks = max(
        1, int(vad.min_silence_ms / 1000 * sample_rate / block_size)
    )
    max_buffer_blocks = int(vad.max_buffer_seconds * sample_rate / block_size)

    chunks = []
    chunk_start = 0

    for i in range(len(energies)):
        blocks_in_chunk = i - chunk_start + 1

        # Check if we have a silence run ending at block i
        if energies[i] < vad.silence_threshold:
            silent_run = 0
            for j in range(i, chunk_start - 1, -1):
                if energies[j] < vad.silence_threshold:
                    silent_run += 1
                else:
                    break
            if (
                silent_run >= min_silence_blocks
                and blocks_in_chunk > min_silence_blocks
            ):
                # Split at start of silence
                split_block = i - silent_run + 1
                if split_block > chunk_start:
                    chunk_audio = audio[
                        chunk_start * block_size : split_block * block_size
                    ]
                    if len(chunk_audio) > 0:
                        chunks.append(chunk_audio)
                    chunk_start = split_block

        # Hard split if buffer too long
        if blocks_in_chunk >= max_buffer_blocks:
            chunk_audio = audio[chunk_start * block_size : (i + 1) * block_size]
            if len(chunk_audio) > 0:
                chunks.append(chunk_audio)
            chunk_start = i + 1

    # Remainder
    if chunk_start < n_blocks:
        remainder = audio[chunk_start * block_size :]
        if len(remainder) > 0:
            chunks.append(remainder)

    return chunks


def run_benchmark(
    audio: np.ndarray,
    reference: str,
    model,
    chunk_seconds: int,
    *,
    vad: VADConfig | None = None,
) -> BenchmarkResult:
    """Transcribe audio in chunks, simulating real-time batching."""
    sample_rate = 16000
    total_audio_s = len(audio) / sample_rate
    backend = "parakeet"

    if vad is not None:
        audio_chunks = vad_split(audio, sample_rate, vad)
        n_chunks = len(audio_chunks)
        chunk_label = f"{n_chunks} VAD chunks ({vad.label})"
    else:
        chunk_samples = chunk_seconds * sample_rate
        n_chunks = max(1, len(audio) // chunk_samples)
        audio_chunks = [
            audio[i * chunk_samples : (i + 1) * chunk_samples] for i in range(n_chunks)
        ]
        chunk_label = f"{n_chunks} chunks @ {chunk_seconds}s"

    print(f"\n{'=' * 70}")
    print(f"  {backend.upper()}  |  {total_audio_s:.0f}s audio  |  {chunk_label}")
    print(f"{'=' * 70}")

    monitor = ResourceMonitor(interval=0.25)

    # Reset MLX peak memory counter
    try:
        import mlx.core as mx

        mx.metal.reset_peak_memory()
    except Exception:
        pass

    monitor.start()
    chunk_results = []
    all_hypotheses = []

    for i, chunk in enumerate(audio_chunks):
        chunk_dur = len(chunk) / sample_rate

        t0 = time.perf_counter()
        hyp = transcribe_parakeet(model, chunk)
        wall = time.perf_counter() - t0
        rtf = wall / chunk_dur

        cr = ChunkResult(
            chunk_idx=i,
            audio_duration=chunk_dur,
            wall_seconds=wall,
            rtf=rtf,
            hypothesis=hyp,
        )
        chunk_results.append(cr)
        all_hypotheses.append(hyp)

        status = "OK" if rtf < 1.0 else "SLOW"
        print(
            f"  [{i + 1:3d}/{n_chunks}] {chunk_dur:5.1f}s audio | "
            f"{wall:6.2f}s wall | RTF {rtf:.3f} | {status}"
        )

    snapshots = monitor.stop()
    resources = summarize_resources(snapshots)
    total_wall = sum(cr.wall_seconds for cr in chunk_results)
    full_hyp = " ".join(all_hypotheses)
    wer = word_error_rate(reference, full_hyp)

    # Per-chunk stats
    rtfs = [cr.rtf for cr in chunk_results]
    walls = [cr.wall_seconds for cr in chunk_results]

    print("\n  Transcription:")
    print(f"    Total audio:     {total_audio_s:8.1f}s  ({total_audio_s / 60:.1f}m)")
    print(f"    Total wall:      {total_wall:8.1f}s  ({total_wall / 60:.1f}m)")
    print(f"    Overall RTF:     {total_wall / total_audio_s:8.3f}x")
    print(
        f"    Per-chunk RTF:   {min(rtfs):.3f} / {sum(rtfs) / len(rtfs):.3f} / "
        f"{max(rtfs):.3f}  (min/mean/max)"
    )
    print(
        f"    Per-chunk wall:  {min(walls):.2f} / {sum(walls) / len(walls):.2f} / "
        f"{max(walls):.2f}s  (min/mean/max)"
    )
    print(f"    WER:             {wer:8.1%}")

    print("\n  Resources:")
    print(
        f"    CPU:             {resources.get('cpu_mean', 0):8.0f}% mean  /  "
        f"{resources.get('cpu_max', 0):.0f}% peak"
    )
    print(
        f"    RSS memory:      {resources.get('rss_mean_mb', 0):8.0f} MB mean  /  "
        f"{resources.get('rss_max_mb', 0):.0f} MB peak"
    )
    if "gpu_active_mean_mb" in resources:
        gpu_active = resources["gpu_active_mean_mb"]
        gpu_peak = resources.get("gpu_peak_mb", 0)
        print(
            f"    GPU memory:      {gpu_active:8.0f} MB active"
            f"  /  {gpu_peak:.0f} MB peak"
        )

    return BenchmarkResult(
        backend=backend,
        total_audio_s=total_audio_s,
        total_wall_s=total_wall,
        rtf=total_wall / total_audio_s,
        wer=wer,
        chunk_results=chunk_results,
        resources=resources,
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def _sweep_configs(
    sweep_range: tuple[int, int, int] = (300, 800, 50),
    sweep_param: str = "silence",
    threshold: float = VAD_SILENCE_THRESHOLD,
    silence_ms: int = VAD_MIN_SILENCE_MS,
    max_buffer: int = VAD_MAX_BUFFER_SECONDS,
) -> list[VADConfig]:
    """Generate sweep configs across a single parameter."""
    start, stop, step = sweep_range
    configs = []
    for val in range(start, stop + 1, step):
        if sweep_param == "silence":
            configs.append(
                VADConfig(
                    silence_threshold=threshold,
                    min_silence_ms=val,
                    max_buffer_seconds=max_buffer,
                )
            )
        elif sweep_param == "max-buffer":
            configs.append(
                VADConfig(
                    silence_threshold=threshold,
                    min_silence_ms=silence_ms,
                    max_buffer_seconds=val,
                )
            )
    return configs


def _print_compare_table(results: list[tuple[str, BenchmarkResult]]) -> None:
    """Print a comparison table from multi-model results."""
    print(f"\n{'=' * 72}")
    print("  MODEL COMPARISON RESULTS")
    print(f"{'=' * 72}")
    col = 38
    print(f"  {'Model':<{col}} {'WER':>7} {'RTF':>7} {'GPU MB':>7}")
    print(f"  {'-' * col} {'-' * 7} {'-' * 7} {'-' * 7}")
    for model_id, result in results:
        gpu = result.resources.get("gpu_peak_mb", 0)
        print(f"  {model_id:<{col}} {result.wer:>6.1%} {result.rtf:>7.3f} {gpu:>7.0f}")
    print()


def _print_sweep_table(results: list[tuple[VADConfig, BenchmarkResult]]) -> None:
    """Print a comparison table from sweep results."""
    print(f"\n{'=' * 80}")
    print("  VAD PARAMETER SWEEP RESULTS")
    print(f"{'=' * 80}")
    print(f"  {'Config':<45} {'WER':>7} {'RTF':>7} {'Chunks':>7} {'GPU MB':>7}")
    print(f"  {'-' * 45} {'-' * 7} {'-' * 7} {'-' * 7} {'-' * 7}")
    for vad_cfg, result in results:
        gpu = result.resources.get("gpu_peak_mb", 0)
        n_chunks = len(result.chunk_results)
        print(
            f"  {vad_cfg.label:<45} {result.wer:>6.1%} "
            f"{result.rtf:>7.3f} {n_chunks:>7} {gpu:>7.0f}"
        )
    print()
    best = min(results, key=lambda x: x[1].wer)
    print(f"  Best WER: {best[1].wer:.1%} — {best[0].label}")


def main():
    parser = argparse.ArgumentParser(
        description="Benchmark parakeet-mlx on LibriSpeech"
    )
    parser.add_argument(
        "--minutes",
        type=float,
        default=3.0,
        help="Minutes of audio to benchmark",
    )
    parser.add_argument(
        "--chunk",
        type=int,
        default=DEFAULT_CHUNK_SECONDS,
        help="Chunk size in seconds (fixed chunking mode)",
    )
    parser.add_argument(
        "--vad",
        action="store_true",
        help="Use VAD-based silence chunking instead of fixed intervals",
    )
    parser.add_argument(
        "--model",
        type=str,
        default="mlx-community/parakeet-tdt-1.1b",
        help="Parakeet model ID (default: 1.1B)",
    )
    parser.add_argument(
        "--silence-threshold",
        type=float,
        default=VAD_SILENCE_THRESHOLD,
        help=f"VAD silence RMS threshold (default: {VAD_SILENCE_THRESHOLD})",
    )
    parser.add_argument(
        "--min-silence-ms",
        type=int,
        default=VAD_MIN_SILENCE_MS,
        help=f"Minimum silence duration in ms (default: {VAD_MIN_SILENCE_MS})",
    )
    parser.add_argument(
        "--max-buffer-seconds",
        type=int,
        default=VAD_MAX_BUFFER_SECONDS,
        help=f"Max buffer before hard drain (default: {VAD_MAX_BUFFER_SECONDS})",
    )
    parser.add_argument(
        "--sweep",
        action="store_true",
        help="Run a sweep across min-silence-ms values",
    )
    parser.add_argument(
        "--sweep-start",
        type=int,
        default=300,
        help="Sweep start for min-silence-ms (default: 300)",
    )
    parser.add_argument(
        "--sweep-stop",
        type=int,
        default=800,
        help="Sweep stop for min-silence-ms (default: 800)",
    )
    parser.add_argument(
        "--sweep-step",
        type=int,
        default=50,
        help="Sweep step (default: 50)",
    )
    parser.add_argument(
        "--sweep-param",
        type=str,
        default="silence",
        choices=["silence", "max-buffer"],
        help="Which parameter to sweep (default: silence)",
    )
    parser.add_argument(
        "--compare",
        type=str,
        default=None,
        help="Comma-separated model IDs to compare (runs benchmark for each)",
    )
    parser.add_argument(
        "--list-models",
        action="store_true",
        help="Print known Parakeet MLX model IDs and exit",
    )
    args = parser.parse_args()

    if args.list_models:
        print("Known Parakeet MLX models:")
        for m in KNOWN_MODELS:
            print(f"  {m}")
        raise SystemExit(0)

    if not LIBRISPEECH_ROOT.exists():
        print(f"ERROR: {LIBRISPEECH_ROOT} not found.")
        print("Run: cd benchmarks/data && tar -xzf test-clean.tar.gz")
        raise SystemExit(1)

    utterances = load_all_utterances()
    target_s = args.minutes * 60
    audio, reference = build_stream(utterances, target_s)
    actual_s = len(audio) / 16000
    print(
        f"Built {actual_s:.0f}s ({actual_s / 60:.1f}m) audio stream from "
        f"LibriSpeech test-clean"
    )

    if args.compare:
        model_ids = [m.strip() for m in args.compare.split(",") if m.strip()]
        vad_cfg = None
        if args.vad:
            vad_cfg = VADConfig(
                silence_threshold=args.silence_threshold,
                min_silence_ms=args.min_silence_ms,
                max_buffer_seconds=args.max_buffer_seconds,
            )
        compare_results = []
        for model_id in model_ids:
            print(f"\nLoading model: {model_id}")
            model = make_parakeet(model_id)
            result = run_benchmark(audio, reference, model, args.chunk, vad=vad_cfg)
            compare_results.append((model_id, result))
            del model
            gc.collect()
            try:
                import mlx.core as mx

                mx.metal.reset_peak_memory()
            except Exception:
                pass
        _print_compare_table(compare_results)
        return

    print(f"\nLoading model: {args.model}")
    model = make_parakeet(args.model)

    if args.sweep:
        configs = _sweep_configs(
            sweep_range=(args.sweep_start, args.sweep_stop, args.sweep_step),
            sweep_param=args.sweep_param,
            threshold=args.silence_threshold,
            silence_ms=args.min_silence_ms,
            max_buffer=args.max_buffer_seconds,
        )
        results = []
        for cfg in configs:
            result = run_benchmark(audio, reference, model, args.chunk, vad=cfg)
            results.append((cfg, result))
        _print_sweep_table(results)
    elif args.vad:
        vad_cfg = VADConfig(
            silence_threshold=args.silence_threshold,
            min_silence_ms=args.min_silence_ms,
            max_buffer_seconds=args.max_buffer_seconds,
        )
        chunks_preview = vad_split(audio, vad=vad_cfg)
        print(f"Chunking: VAD ({vad_cfg.label}) → {len(chunks_preview)} chunks")
        run_benchmark(audio, reference, model, args.chunk, vad=vad_cfg)
    else:
        print(f"Chunking: fixed {args.chunk}s")
        run_benchmark(audio, reference, model, args.chunk)

    del model
    gc.collect()


if __name__ == "__main__":
    main()
