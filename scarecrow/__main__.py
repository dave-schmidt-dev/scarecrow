"""Entry point for `python -m scarecrow` and the `scarecrow` console script."""

import logging
import sys


def main() -> None:
    # Suppress ctranslate2 C++ warnings (float16→float32 on Apple Silicon).
    import ctranslate2

    ctranslate2.set_log_level(logging.ERROR)

    from scarecrow.transcriber import Transcriber

    # Create and prepare the transcriber BEFORE Textual starts.
    # AudioToTextRecorder uses multiprocessing.Value which creates
    # semaphores — this breaks if Textual has already modified FDs.
    print("Loading speech models…")
    transcriber = Transcriber()
    try:
        transcriber.prepare()
    except Exception as e:
        print(f"Failed to start transcriber: {e}", file=sys.stderr)
        sys.exit(1)

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
