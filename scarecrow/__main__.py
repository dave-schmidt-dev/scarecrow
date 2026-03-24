"""Entry point for `python -m scarecrow` and the `scarecrow` console script."""

import logging
import os
import signal
import sys


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

    # Redirect stderr briefly during model load to suppress C++ warnings
    stderr_fd = sys.stderr.fileno()
    saved_stderr = os.dup(stderr_fd)
    devnull = os.open(os.devnull, os.O_WRONLY)
    os.dup2(devnull, stderr_fd)
    os.close(devnull)

    from scarecrow.transcriber import Transcriber

    print("Loading speech models…")
    transcriber = Transcriber()
    try:
        transcriber.prepare()
    except Exception as e:
        os.dup2(saved_stderr, stderr_fd)
        os.close(saved_stderr)
        print(f"Failed to start transcriber: {e}", file=sys.stderr)
        sys.exit(1)

    # Restore stderr
    os.dup2(saved_stderr, stderr_fd)
    os.close(saved_stderr)

    print("Models loaded. Starting TUI…")

    from scarecrow.app import ScarecrowApp

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
