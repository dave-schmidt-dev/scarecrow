"""Entry point for `python -m scarecrow` and the `scarecrow` console script."""

import logging
import os
import signal
import sys
from datetime import datetime
from pathlib import Path


def _model_cache_path(model_name: str) -> Path | None:
    """Return the HuggingFace cache path for a model, or None if not cached."""
    cache_dir = Path.home() / ".cache" / "huggingface" / "hub"
    path = cache_dir / f"models--Systran--faster-whisper-{model_name}"
    return path if path.exists() else None


def main() -> None:
    # Log to file for debugging (Textual owns the terminal)
    logging.basicConfig(
        filename="scarecrow_debug.log",
        level=logging.DEBUG,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    # Suppress ctranslate2 C++ warnings (float16→float32 on Apple Silicon).
    import ctranslate2

    ctranslate2.set_log_level(logging.ERROR)

    from scarecrow import config

    print(flush=True)
    print("  Scarecrow", flush=True)
    print("  " + "─" * 40, flush=True)
    live = config.REALTIME_MODEL
    batch = config.FINAL_MODEL
    print(f"  Live model:   {live} (always-on, real-time)", flush=True)
    print(f"  Batch model:  {batch} (accurate, every 30s)", flush=True)

    # Show cache status for each model
    models = [("Live", live), ("Batch", batch)]
    for label, model in models:
        cache = _model_cache_path(model)
        if cache:
            print(f"  {label} cache:  {cache}", flush=True)
        else:
            msg = "not cached — will download on first run"
            print(f"  {label} cache:  {msg}", flush=True)

    # Show where recordings will be saved
    recordings_dir = config.DEFAULT_RECORDINGS_DIR.resolve()
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    print(f"  Recordings:   {recordings_dir}/", flush=True)
    print(f"  This session: {recordings_dir}/{timestamp}/", flush=True)
    print(flush=True)

    from scarecrow.transcriber import Transcriber

    print("  Loading speech models…", flush=True)

    # Suppress ctranslate2 C++ float16→float32 warning during model load only
    stderr_fd = sys.stderr.fileno()
    saved_stderr = os.dup(stderr_fd)
    devnull = os.open(os.devnull, os.O_WRONLY)
    os.dup2(devnull, stderr_fd)
    os.close(devnull)

    transcriber = Transcriber()
    try:
        transcriber.prepare()
    except Exception as e:
        os.dup2(saved_stderr, stderr_fd)
        os.close(saved_stderr)
        print(f"Failed to start transcriber: {e}", file=sys.stderr)
        sys.exit(1)

    os.dup2(saved_stderr, stderr_fd)
    os.close(saved_stderr)

    print("  Models loaded. Starting TUI…")
    print()

    # Suppress resource_tracker warnings on exit (leaked semaphores from
    # RealtimeSTT's multiprocessing are cleaned up by our SIGKILL anyway).
    import warnings

    from scarecrow.app import ScarecrowApp

    warnings.filterwarnings("ignore", "resource_tracker:.*semaphore", UserWarning)

    app = ScarecrowApp(transcriber=transcriber)
    try:
        app.run()
    except KeyboardInterrupt:
        pass
    finally:
        transcriber.shutdown()
        # Force exit — RealtimeSTT daemon threads can hang on join
        os.kill(os.getpid(), signal.SIGKILL)


if __name__ == "__main__":
    main()
