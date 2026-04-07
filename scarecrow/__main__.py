"""Entry point for `python -m scarecrow` and the `scarecrow` console script."""

from __future__ import annotations

import contextlib
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

from scarecrow.runtime import configure_runtime_environment

configure_runtime_environment()


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


def main() -> None:
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
