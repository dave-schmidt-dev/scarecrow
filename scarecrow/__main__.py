"""Entry point for `python -m scarecrow` and the `scarecrow` console script."""

import logging
import sys
import time
from datetime import datetime
from pathlib import Path


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
        print("  Done.", flush=True)


if __name__ == "__main__":
    main()
