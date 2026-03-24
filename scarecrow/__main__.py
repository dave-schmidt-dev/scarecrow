"""Entry point for `python -m scarecrow` and the `scarecrow` console script."""

import logging
import os
import sys


def main() -> None:
    # Suppress ctranslate2 C++ float16 warnings written directly to stderr.
    # These fire because Apple Silicon doesn't have efficient float16 in
    # CTranslate2's CPU backend — harmless but noisy.
    import ctranslate2

    ctranslate2.set_log_level(logging.ERROR)
    os.environ["CT2_VERBOSE"] = "-1"

    from scarecrow.transcriber import Transcriber

    # Create and prepare the transcriber BEFORE Textual starts.
    # AudioToTextRecorder uses multiprocessing.Value which creates
    # semaphores — this breaks if Textual has already modified FDs.
    print("Loading speech models…")

    # Temporarily redirect stderr to suppress C++ warnings during model load
    stderr_fd = sys.stderr.fileno()
    saved_stderr = os.dup(stderr_fd)
    devnull = os.open(os.devnull, os.O_WRONLY)
    os.dup2(devnull, stderr_fd)
    os.close(devnull)

    transcriber = Transcriber()
    try:
        transcriber.prepare()
    except Exception as e:
        # Restore stderr before printing error
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
        # Always clean up transcriber — releases mic and model resources
        transcriber.shutdown()


if __name__ == "__main__":
    main()
