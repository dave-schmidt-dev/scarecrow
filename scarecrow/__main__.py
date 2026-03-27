"""Entry point for `python -m scarecrow` and the `scarecrow` console script."""

from __future__ import annotations

import contextlib
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

from scarecrow.runtime import configure_runtime_environment, model_cache_path

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


def _model_cache_path(model_name: str) -> Path | None:
    """Backward-compatible wrapper used by tests and startup output."""
    return model_cache_path(model_name)


def main() -> None:
    log_path = Path.home() / ".cache" / "scarecrow" / "debug.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=str(log_path),
        level=logging.DEBUG,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    from scarecrow import config
    from scarecrow.app import ScarecrowApp
    from scarecrow.transcriber import Transcriber

    print(flush=True)
    print("  Scarecrow", flush=True)
    print("  " + "─" * 40, flush=True)
    batch = config.FINAL_MODEL
    print(f"  Batch model:  {batch} (accurate, every 15s)", flush=True)

    cache = _model_cache_path(batch)
    if cache is not None:
        print(f"  Batch cache:  {cache}", flush=True)
    else:
        print(
            "  Batch cache:  not cached — will download on first run",
            flush=True,
        )

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

    t1 = time.monotonic()
    print(f"  Ready ({t1 - t0:.1f}s)", flush=True)
    print(f"  Batch model ({batch}) loads on first batch run", flush=True)
    print("  Starting TUI…", flush=True)
    print(flush=True)

    app = ScarecrowApp(transcriber=transcriber)
    try:
        app.run()
    except KeyboardInterrupt:
        pass
    finally:
        print("\n  Shutting down…", flush=True)
        try:
            app.cleanup_after_exit()
        except Exception:
            logging.getLogger(__name__).exception("Finally: failed to clean up app")
        if app._shutdown_summary:
            print(app._shutdown_summary, flush=True)
        print("  Done.", flush=True)
        print(flush=True)
        print("  Press Enter to close (auto-close in 30s)…", flush=True)
        _wait_for_enter_or_timeout(30)


if __name__ == "__main__":
    main()
