#!/usr/bin/env python3
"""Benchmark parakeet-mlx vs faster-whisper on LibriSpeech test-clean.

Concatenates utterances into long audio streams (default ~3 min each),
then transcribes in batch chunks (5s for parakeet, 15s for whisper)
to simulate real scarecrow usage. Tracks speed, accuracy, CPU, memory,
and GPU memory throughout.

Usage:
    uv run python benchmarks/bench_librispeech.py [--minutes N] [--backend both]
"""

from __future__ import annotations

import argparse
import gc
import os
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

# VAD parameters matching config.py defaults
VAD_SILENCE_THRESHOLD = 0.01
VAD_MIN_SILENCE_MS = 300
VAD_MAX_BUFFER_SECONDS = 8


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
    _has_mlx: bool = False

    def start(self, has_mlx: bool = False):
        self._has_mlx = has_mlx
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
            if self._has_mlx:
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
# Backends
# ---------------------------------------------------------------------------


def make_whisper():
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    from faster_whisper import WhisperModel

    return WhisperModel("large-v3-turbo", device="auto", compute_type="auto")


def make_parakeet():
    from parakeet_mlx import from_pretrained

    return from_pretrained("mlx-community/parakeet-tdt-0.6b-v3")


def transcribe_whisper(model, audio: np.ndarray) -> str:
    segments, _ = model.transcribe(
        audio,
        language="en",
        beam_size=5,
        vad_filter=False,
        condition_on_previous_text=False,
    )
    return " ".join(seg.text.strip() for seg in segments).strip()


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


def vad_split(audio: np.ndarray, sample_rate: int = 16000) -> list[np.ndarray]:
    """Split audio at silence boundaries, mimicking recorder VAD logic."""
    block_size = 512  # ~32ms at 16kHz, typical PortAudio callback size
    n_blocks = len(audio) // block_size

    # Compute per-block RMS
    energies = []
    for i in range(n_blocks):
        block = audio[i * block_size : (i + 1) * block_size]
        rms = float(np.sqrt(np.mean(block**2)))
        energies.append(rms)

    min_silence_blocks = max(
        1, int(VAD_MIN_SILENCE_MS / 1000 * sample_rate / block_size)
    )
    max_buffer_blocks = int(VAD_MAX_BUFFER_SECONDS * sample_rate / block_size)

    chunks = []
    chunk_start = 0

    for i in range(len(energies)):
        blocks_in_chunk = i - chunk_start + 1

        # Check if we have a silence run ending at block i
        if energies[i] < VAD_SILENCE_THRESHOLD:
            silent_run = 0
            for j in range(i, chunk_start - 1, -1):
                if energies[j] < VAD_SILENCE_THRESHOLD:
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
    backend: str,
    audio: np.ndarray,
    reference: str,
    model,
    transcribe_fn,
    chunk_seconds: int,
    *,
    use_vad: bool = False,
) -> BenchmarkResult:
    """Transcribe audio in chunks, simulating real-time batching."""
    sample_rate = 16000
    total_audio_s = len(audio) / sample_rate

    if use_vad:
        audio_chunks = vad_split(audio, sample_rate)
        n_chunks = len(audio_chunks)
        chunk_label = f"{n_chunks} VAD chunks"
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
    has_mlx = backend == "parakeet"

    # Reset MLX peak memory counter
    if has_mlx:
        try:
            import mlx.core as mx

            mx.metal.reset_peak_memory()
        except Exception:
            pass

    monitor.start(has_mlx=has_mlx)
    chunk_results = []
    all_hypotheses = []

    for i, chunk in enumerate(audio_chunks):
        chunk_dur = len(chunk) / sample_rate

        t0 = time.perf_counter()
        hyp = transcribe_fn(model, chunk)
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


def print_comparison(results: list[BenchmarkResult]):
    print(f"\n{'=' * 70}")
    print("  COMPARISON")
    print(f"{'=' * 70}")

    headers = [""] + [r.backend.upper() for r in results]
    rows = [
        ("RTF (overall)", [f"{r.rtf:.3f}x" for r in results]),
        ("WER", [f"{r.wer:.1%}" for r in results]),
        ("Wall time", [f"{r.total_wall_s:.1f}s" for r in results]),
        ("CPU mean", [f"{r.resources.get('cpu_mean', 0):.0f}%" for r in results]),
        ("CPU peak", [f"{r.resources.get('cpu_max', 0):.0f}%" for r in results]),
        ("RSS peak", [f"{r.resources.get('rss_max_mb', 0):.0f} MB" for r in results]),
        (
            "GPU peak",
            [
                f"{r.resources.get('gpu_peak_mb', 0):.0f} MB"
                if "gpu_peak_mb" in r.resources
                else "N/A"
                for r in results
            ],
        ),
    ]

    col_w = 14
    print(f"  {'':22s}" + "".join(f"{h:>{col_w}s}" for h in headers[1:]))
    for label, vals in rows:
        print(f"  {label:22s}" + "".join(f"{v:>{col_w}s}" for v in vals))

    if len(results) == 2:
        p, w = results[0], results[1]
        speedup = w.total_wall_s / p.total_wall_s if p.total_wall_s > 0 else 0
        print(
            f"\n  {p.backend} is {speedup:.1f}x "
            f"{'faster' if speedup > 1 else 'slower'} than {w.backend}"
        )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Benchmark ASR backends on LibriSpeech"
    )
    parser.add_argument(
        "--minutes",
        type=float,
        default=3.0,
        help="Minutes of audio to benchmark per backend",
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
        "--backend", choices=["parakeet", "whisper", "both"], default="both"
    )
    args = parser.parse_args()

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
    if args.vad:
        chunks_preview = vad_split(audio)
        print(
            f"Chunking: VAD (silence threshold={VAD_SILENCE_THRESHOLD}, "
            f"min silence={VAD_MIN_SILENCE_MS}ms, max={VAD_MAX_BUFFER_SECONDS}s) "
            f"→ {len(chunks_preview)} chunks"
        )
    else:
        print(f"Chunking: fixed {args.chunk}s  |  No overlap  |  No context injection")

    results = []

    if args.backend in ("parakeet", "both"):
        print("\nLoading parakeet model...")
        model = make_parakeet()
        results.append(
            run_benchmark(
                "parakeet",
                audio,
                reference,
                model,
                transcribe_parakeet,
                args.chunk,
                use_vad=args.vad,
            )
        )
        del model
        gc.collect()

    if args.backend in ("whisper", "both"):
        print("\nLoading whisper model...")
        model = make_whisper()
        results.append(
            run_benchmark(
                "whisper",
                audio,
                reference,
                model,
                transcribe_whisper,
                args.chunk,
                use_vad=args.vad,
            )
        )
        del model
        gc.collect()

    if len(results) == 2:
        print_comparison(results)


if __name__ == "__main__":
    main()
