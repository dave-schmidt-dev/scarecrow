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

    # Switch output to Scarecrow Multi-Output Device for sys audio capture
    audio_switch = None
    if sys_audio:
        from scarecrow.audio_routing import activate_scarecrow_output

        audio_switch = activate_scarecrow_output()
        if audio_switch:
            print("  System audio: routing via Scarecrow Output", flush=True)
        else:
            print("  System audio: Scarecrow Output not found — mic only", flush=True)

    t1 = time.monotonic()
    print(f"  Ready ({t1 - t0:.1f}s)", flush=True)
    print("  Starting TUI…", flush=True)
    print(flush=True)

    app = ScarecrowApp(transcriber=transcriber, sys_audio=sys_audio)
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
            # Restore audio output immediately after stopping recording,
            # before slow Phase 2 work (compression + summarization).
            if audio_switch is not None:
                from scarecrow.audio_routing import restore_output

                print("  Restoring audio output…", flush=True)
                try:
                    restore_output(audio_switch)
                    audio_switch = None  # prevent double-restore
                except Exception:
                    logging.getLogger(__name__).exception(
                        "Failed to restore audio output"
                    )
            try:
                app.post_exit_cleanup()  # Phase 2: compress + maybe summarize
            except Exception:
                logging.getLogger(__name__).exception("Phase 2 cleanup failed")
            if app._shutdown_summary:
                print(app._shutdown_summary, flush=True)
            if getattr(app, "_summary_path", None):
                print(f"  Summary: {app._summary_path}", flush=True)
            print("  Done.", flush=True)
        # Restore audio output (discard path, or Phase 1 restore skipped)
        if audio_switch is not None:
            from scarecrow.audio_routing import restore_output

            print("  Restoring audio output…", flush=True)
            try:
                restore_output(audio_switch)
            except Exception:
                logging.getLogger(__name__).exception("Failed to restore audio output")
        print(flush=True)
        print("  Press Enter to close (auto-close in 30s)…", flush=True)
        _wait_for_enter_or_timeout(30)


if __name__ == "__main__":
    main()
