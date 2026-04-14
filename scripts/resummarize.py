#!/usr/bin/env python3
"""Re-run diarization and/or summarization on an existing session directory.

Usage:
    python3 scripts/resummarize.py ~/recordings/2026-03-29_14-30-00
    python3 scripts/resummarize.py ~/recordings/2026-03-29_14-30-00 --diarize
    python3 scripts/resummarize.py ~/recordings/2026-03-29_14-30-00 --model gemma4
    python3 scripts/resummarize.py ~/recordings/2026-03-29_14-30-00 --backend mlx
"""

from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def _pop_flag(args: list[str], flag: str) -> bool:
    """Remove a boolean flag from args, returning True if it was present."""
    if flag in args:
        args.remove(flag)
        return True
    return False


def _pop_option(args: list[str], flag: str) -> str | None:
    """Remove a --key value pair from args, returning the value or None."""
    if flag not in args:
        return None
    idx = args.index(flag)
    if idx + 1 >= len(args):
        print(f"{flag} requires a value", file=sys.stderr)
        sys.exit(1)
    value = args[idx + 1]
    del args[idx : idx + 2]
    return value


def _print_progress(msg: str) -> None:
    print(f"  {msg}", flush=True)


def _detect_sys_audio(session_dir: Path, n_segments: int) -> bool:
    """Check whether the session has system audio files."""
    for seg in range(1, n_segments + 1):
        suffix = f"_seg{seg}" if seg > 1 else ""
        if (session_dir / f"audio_sys{suffix}.flac").exists():
            return True
    return False


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    args = sys.argv[1:]
    diarize = _pop_flag(args, "--diarize")
    model = _pop_option(args, "--model")
    backend = _pop_option(args, "--backend")

    if len(args) != 1:
        print(
            f"Usage: {sys.argv[0]} <session-dir> [--diarize] "
            f"[--model gemma4] [--backend gguf|mlx]",
            file=sys.stderr,
        )
        return 1

    session_dir = Path(args[0]).resolve()
    if not session_dir.is_dir():
        print(f"Not a directory: {session_dir}", file=sys.stderr)
        return 1

    transcript = session_dir / "transcript.jsonl"
    if not transcript.exists():
        print(f"No transcript.jsonl in {session_dir}", file=sys.stderr)
        return 1

    import json

    from scarecrow.config import OBSIDIAN_VAULT_DIR
    from scarecrow.summarizer import summarize_session, summarize_session_segments

    # Detect segmented sessions by counting segment_boundary events
    n_segments = 1
    with transcript.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
                if ev.get("type") == "segment_boundary":
                    n_segments += 1
            except json.JSONDecodeError:
                pass

    # Run diarization if requested
    if diarize:
        from scarecrow.diarizer import _read_events as _diar_read_events
        from scarecrow.diarizer import diarize_session

        events = _diar_read_events(transcript)
        sys_audio = _detect_sys_audio(session_dir, n_segments)

        print(f"Diarizing {session_dir} (segments={n_segments})...")
        t0 = time.monotonic()
        diarize_session(
            session_dir,
            n_segments,
            events,
            sys_audio_enabled=sys_audio,
            progress_callback=_print_progress,
        )
        elapsed = time.monotonic() - t0
        print(f"  Diarization finished in {elapsed:.1f}s")

    # When benchmarking with --model, write to summary_<model>.md
    output_name = f"summary_{model}.md" if model else "summary.md"

    print(
        f"Summarizing {session_dir} "
        f"(model={model or 'default'}, backend={backend or 'default'}, "
        f"segments={n_segments})..."
    )

    if n_segments > 1 and not model:
        result = summarize_session_segments(
            session_dir,
            n_segments,
            obsidian_dir=OBSIDIAN_VAULT_DIR,
            backend=backend,
            progress_callback=_print_progress,
        )
    else:
        if n_segments > 1 and model:
            print(
                "  Note: --model ignores segment boundaries "
                "(processing as single transcript)",
                file=sys.stderr,
            )
        result = summarize_session(
            session_dir,
            obsidian_dir=OBSIDIAN_VAULT_DIR,
            model=model,
            output_name=output_name,
            backend=backend,
            progress_callback=_print_progress,
        )

    if result:
        print(f"Summary written to {result}")
        return 0
    else:
        print("Summarization failed. Check summary.md for details.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
