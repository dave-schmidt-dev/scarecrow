"""Entry point for `python -m scarecrow` and the `scarecrow` console script."""

from __future__ import annotations

import contextlib
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

from scarecrow.runtime import configure_runtime_environment

configure_runtime_environment()


# ---------------------------------------------------------------------------
# Subcommands — dispatched before any TUI/model setup
# ---------------------------------------------------------------------------


def _resolve_session_dir(args: list[str]) -> Path:
    """Resolve a session directory from args: explicit path or --latest."""
    from scarecrow import config

    if "--latest" in args:
        args.remove("--latest")
        recordings = config.DEFAULT_RECORDINGS_DIR
        if not recordings.is_dir():
            print(f"  Recordings directory not found: {recordings}", file=sys.stderr)
            sys.exit(1)
        # Find most recent session by modification time
        sessions = sorted(
            (d for d in recordings.iterdir() if d.is_dir()),
            key=lambda d: d.stat().st_mtime,
            reverse=True,
        )
        if not sessions:
            print(f"  No sessions found in {recordings}", file=sys.stderr)
            sys.exit(1)
        return sessions[0]

    # Remaining args after flags are stripped should be [session-dir]
    dirs = [a for a in args if not a.startswith("-")]
    if len(dirs) != 1:
        print(
            "Usage: scarecrow reprocess <session-dir> | --latest "
            "[--no-diarize] [--model X] [--backend X]",
            file=sys.stderr,
        )
        sys.exit(1)
    path = Path(dirs[0]).resolve()
    if not path.is_dir():
        print(f"  Not a directory: {path}", file=sys.stderr)
        sys.exit(1)
    return path


def _count_segments(transcript: Path) -> int:
    """Count segments by tallying segment_boundary events."""
    n = 1
    with transcript.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                if json.loads(line).get("type") == "segment_boundary":
                    n += 1
            except json.JSONDecodeError:
                pass
    return n


def _detect_sys_audio(session_dir: Path, n_segments: int) -> bool:
    """Check whether the session has system audio files."""
    for seg in range(1, n_segments + 1):
        suffix = f"_seg{seg}" if seg > 1 else ""
        if (session_dir / f"audio_sys{suffix}.flac").exists():
            return True
    return False


def _print_progress(msg: str) -> None:
    print(f"  {msg}", flush=True)


def _cmd_reprocess(args: list[str]) -> None:
    """Re-run diarization and/or summarization on an existing session."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    no_diarize = "--no-diarize" in args
    if no_diarize:
        args.remove("--no-diarize")

    model = None
    if "--model" in args:
        idx = args.index("--model")
        if idx + 1 >= len(args):
            print("--model requires a value", file=sys.stderr)
            sys.exit(1)
        model = args[idx + 1]
        del args[idx : idx + 2]

    backend = None
    if "--backend" in args:
        idx = args.index("--backend")
        if idx + 1 >= len(args):
            print("--backend requires a value", file=sys.stderr)
            sys.exit(1)
        backend = args[idx + 1]
        del args[idx : idx + 2]

    session_dir = _resolve_session_dir(args)
    transcript = session_dir / "transcript.jsonl"
    if not transcript.exists():
        print(f"  No transcript.jsonl in {session_dir}", file=sys.stderr)
        sys.exit(1)

    n_segments = _count_segments(transcript)
    print(flush=True)
    print(f"  Reprocessing: {session_dir.name}", flush=True)
    print(f"  Segments: {n_segments}", flush=True)
    print(flush=True)

    t0 = time.monotonic()

    # Diarization
    if not no_diarize:
        from scarecrow.diarizer import _read_events as _diar_read_events
        from scarecrow.diarizer import diarize_session

        events = _diar_read_events(transcript)
        sys_audio = _detect_sys_audio(session_dir, n_segments)

        diar_t0 = time.monotonic()
        diarize_session(
            session_dir,
            n_segments,
            events,
            sys_audio_enabled=sys_audio,
            progress_callback=_print_progress,
        )
        diar_elapsed = time.monotonic() - diar_t0
        print(flush=True)

    # Summarization
    from scarecrow.config import OBSIDIAN_VAULT_DIR
    from scarecrow.summarizer import summarize_session, summarize_session_segments

    output_name = f"summary_{model}.md" if model else "summary.md"

    if n_segments > 1 and not model:
        result = summarize_session_segments(
            session_dir,
            n_segments,
            obsidian_dir=OBSIDIAN_VAULT_DIR,
            backend=backend,
            progress_callback=_print_progress,
        )
    else:
        result = summarize_session(
            session_dir,
            obsidian_dir=OBSIDIAN_VAULT_DIR,
            model=model,
            output_name=output_name,
            backend=backend,
            progress_callback=_print_progress,
        )

    total = time.monotonic() - t0
    print(flush=True)
    if not no_diarize:
        print(f"  Diarization: {diar_elapsed:.1f}s", flush=True)
    if result:
        print(f"  Summary: {result}", flush=True)
    else:
        print("  Summarization failed. Check summary.md for details.", file=sys.stderr)
    print(f"  Total: {total:.1f}s", flush=True)


_SUBCOMMANDS = {
    "reprocess": _cmd_reprocess,
}


def _wait_for_enter_or_timeout(timeout: int = 30) -> None:
    """Wait for Enter key or timeout, whichever comes first."""
    import select
    import termios
    import tty

    fd = sys.stdin.fileno()
    old_settings = None
    try:
        old_settings = termios.tcgetattr(fd)
        tty.setcbreak(fd)
        ready, _, _ = select.select([sys.stdin], [], [], timeout)
        if ready:
            sys.stdin.read(1)
    except (termios.error, OSError, ValueError):
        time.sleep(timeout)
    finally:
        if old_settings is not None:
            with contextlib.suppress(termios.error, OSError, ValueError):
                termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


_HELP = """\
usage: scarecrow [subcommand] [options]

Live captions TUI — launch with no arguments to start recording.

subcommands:
  reprocess <dir> | --latest   Re-run diarization and/or summarization

recording options:
  --no-sys-audio               Disable system audio capture
  --mic-only                   Start with system audio muted
  --sys-only                   Start with microphone muted

reprocess options:
  --latest                     Use most recent session in ~/recordings
  --no-diarize                 Skip diarization, summarize only
  --model <name>               Summarizer model (e.g. gemma4)
  --backend <gguf|mlx>         Summarizer backend

examples:
  scarecrow                    Launch TUI with mic + system audio
  scarecrow reprocess --latest Re-diarize + re-summarize latest session
  scarecrow --help             Show this help
"""


def main() -> None:
    # Help flag
    if len(sys.argv) > 1 and sys.argv[1] in ("--help", "-h"):
        print(_HELP)
        return

    # Dispatch subcommands before any TUI/model setup
    if len(sys.argv) > 1 and sys.argv[1] in _SUBCOMMANDS:
        _SUBCOMMANDS[sys.argv[1]](sys.argv[2:])
        return

    log_path = Path.home() / ".cache" / "scarecrow" / "debug.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=str(log_path),
        level=logging.DEBUG,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    # System audio capture is on by default; --no-sys-audio disables it
    if "--no-sys-audio" in sys.argv:
        sys_audio = False
        sys.argv.remove("--no-sys-audio")
    else:
        sys_audio = True
    # Remove legacy flag if present
    if "--sys-audio" in sys.argv:
        sys.argv.remove("--sys-audio")

    # Launch with one source muted
    mic_muted = False
    sys_muted = False
    if "--mic-only" in sys.argv:
        sys.argv.remove("--mic-only")
        sys_muted = True
    if "--sys-only" in sys.argv:
        sys.argv.remove("--sys-only")
        mic_muted = True

    # Create the system audio tap BEFORE importing anything that touches
    # sounddevice.  PortAudio snapshots the device list at first init and
    # never rescans, so the tap aggregate must exist before that happens.
    tap_handle = None
    if sys_audio:
        from scarecrow.audio_tap import create_system_tap

        tap_handle = create_system_tap()
        if tap_handle is None:
            sys_audio = False  # degrade to mic-only

    from scarecrow import config
    from scarecrow.app import ScarecrowApp
    from scarecrow.transcriber import Transcriber

    print(flush=True)
    print("  Scarecrow", flush=True)
    print("  " + "─" * 40, flush=True)

    print("  Backend:      parakeet-mlx (Apple Silicon GPU)", flush=True)
    print(f"  Model:        {config.PARAKEET_MODEL}", flush=True)
    print("  Chunking:     VAD (drains at speech pauses)", flush=True)

    recordings_dir = config.DEFAULT_RECORDINGS_DIR.resolve()
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    print(f"  Recordings:   {recordings_dir}/", flush=True)
    print(f"  This session: {recordings_dir}/{timestamp}/", flush=True)
    print(flush=True)

    print("  Preparing…", flush=True)
    t0 = time.monotonic()

    transcriber = Transcriber()
    try:
        transcriber.prepare()
    except Exception as exc:
        print(f"Failed to prepare batch transcriber: {exc}", file=sys.stderr)
        sys.exit(1)

    print("  Loading Parakeet model…", flush=True)
    try:
        transcriber.preload_batch_model()
    except Exception as exc:
        print(f"  Failed to load Parakeet model: {exc}", file=sys.stderr)
        sys.exit(1)

    if sys_audio:
        print("  System audio: Process Tap (macOS 14.2+)", flush=True)

    t1 = time.monotonic()
    print(f"  Ready ({t1 - t0:.1f}s)", flush=True)
    print("  Starting TUI…", flush=True)
    print(flush=True)

    app = ScarecrowApp(
        transcriber=transcriber,
        sys_audio=sys_audio,
        mic_muted=mic_muted,
        sys_muted=sys_muted,
        tap_handle=tap_handle,
    )
    try:
        app.run()
    except KeyboardInterrupt:
        pass
    finally:
        if getattr(app, "_discard_mode", False):
            print("\n  Session discarded.", flush=True)
            if app._shutdown_summary:
                print(app._shutdown_summary, flush=True)
        else:
            if getattr(app, "_skip_summary", False):
                print("\n  Shutting down (quick quit)…", flush=True)
            else:
                print("\n  Shutting down…", flush=True)
            try:
                app.cleanup_after_exit()  # Phase 1 safety net (no-op if already ran)
            except Exception:
                logging.getLogger(__name__).exception("Phase 1 cleanup failed")
            try:
                app.post_exit_cleanup()  # Phase 2: compress + maybe summarize
            except Exception:
                logging.getLogger(__name__).exception("Phase 2 cleanup failed")
            # Re-collect metrics after compression so FLAC sizes are shown
            app._shutdown_summary = app._collect_shutdown_metrics()
            if app._shutdown_summary:
                print(app._shutdown_summary, flush=True)
            if getattr(app, "_summary_path", None):
                print(f"  Summary: {app._summary_path}", flush=True)
            print("  Done.", flush=True)
        print(flush=True)
        print("  Press Enter to close (auto-close in 30s)…", flush=True)
        _wait_for_enter_or_timeout(30)


if __name__ == "__main__":
    main()
