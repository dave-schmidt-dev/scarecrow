"""Entry point for `python -m scarecrow` and the `scarecrow` console script."""

import logging
import os
import sys
import time
import warnings
from datetime import datetime
from pathlib import Path

# Suppress HuggingFace Hub authentication warnings
os.environ.setdefault("HF_HUB_DISABLE_IMPLICIT_TOKEN", "1")
warnings.filterwarnings("ignore", message=".*unauthenticated requests.*")


def _wait_for_enter_or_timeout(timeout: int = 30) -> None:
    """Wait for Enter key or timeout, whichever comes first."""
    import contextlib
    import select
    import termios
    import tty

    fd = sys.stdin.fileno()
    try:
        old_settings = termios.tcgetattr(fd)
        # Restore cooked mode so Enter works normally
        tty.setcbreak(fd)
        ready, _, _ = select.select([sys.stdin], [], [], timeout)
        if ready:
            sys.stdin.read(1)
    except (termios.error, OSError, ValueError):
        # Fallback: just sleep if terminal is unavailable
        import time

        time.sleep(timeout)
    finally:
        with contextlib.suppress(termios.error, OSError, ValueError):
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


def _model_cache_path(model_name: str) -> Path | None:
    """Return the HuggingFace cache path for a model, or None if not cached."""
    cache_dir = Path.home() / ".cache" / "huggingface" / "hub"
    path = cache_dir / f"models--Systran--faster-whisper-{model_name}"
    return path if path.exists() else None


def main() -> None:
    logging.basicConfig(
        filename="scarecrow_debug.log",
        level=logging.DEBUG,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    from scarecrow import config

    print(flush=True)
    print("  Scarecrow", flush=True)
    print("  " + "\u2500" * 40, flush=True)
    live = config.REALTIME_MODEL
    batch = config.FINAL_MODEL
    print(f"  Live model:   {live} (always-on, real-time)", flush=True)
    print(f"  Batch model:  {batch} (accurate, every 30s)", flush=True)

    models = [("Live", live), ("Batch", batch)]
    for label, model in models:
        cache = _model_cache_path(model)
        if cache:
            print(f"  {label} cache:  {cache}", flush=True)
        else:
            msg = "not cached \u2014 will download on first run"
            print(f"  {label} cache:  {msg}", flush=True)

    recordings_dir = config.DEFAULT_RECORDINGS_DIR.resolve()
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    print(f"  Recordings:   {recordings_dir}/", flush=True)
    print(f"  This session: {recordings_dir}/{timestamp}/", flush=True)
    print(flush=True)

    print("  Loading models\u2026", flush=True)
    t0 = time.monotonic()

    from scarecrow.transcriber import Transcriber

    transcriber = Transcriber()
    try:
        transcriber.prepare()
    except Exception as e:
        print(f"Failed to start transcriber: {e}", file=sys.stderr)
        sys.exit(1)

    t1 = time.monotonic()
    print(f"  Ready ({t1 - t0:.1f}s)", flush=True)
    print(f"  Batch model ({batch}) loads on first use", flush=True)
    print("  Starting TUI\u2026", flush=True)
    print(flush=True)

    from scarecrow.app import ScarecrowApp

    app = ScarecrowApp(transcriber=transcriber)
    try:
        app.run()
    except KeyboardInterrupt:
        pass
    finally:
        print("\n  Shutting down\u2026", flush=True)
        transcriber.shutdown()
        if hasattr(app, "_shutdown_summary") and app._shutdown_summary:
            print(app._shutdown_summary, flush=True)
        print("  Done.", flush=True)
        print(flush=True)
        print("  Press Enter to close (auto-close in 30s)\u2026", flush=True)
        _wait_for_enter_or_timeout(30)


if __name__ == "__main__":
    main()
