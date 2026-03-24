"""Entry point for `python -m scarecrow` and the `scarecrow` console script."""

import contextlib
import sys


def main() -> None:
    from scarecrow.transcriber import Transcriber

    # Create and prepare the transcriber BEFORE Textual starts.
    # AudioToTextRecorder uses multiprocessing.Value which creates
    # semaphores — this breaks if Textual has already modified FDs.
    print("Loading speech models… (first run downloads ~1.5 GB)")
    transcriber = Transcriber()
    try:
        transcriber.prepare()
    except Exception as e:
        print(f"Failed to start transcriber: {e}", file=sys.stderr)
        sys.exit(1)

    print("Models loaded. Starting TUI…")

    from scarecrow.app import ScarecrowApp

    app = ScarecrowApp(transcriber=transcriber)
    with contextlib.suppress(KeyboardInterrupt):
        app.run()

    # Clean up transcriber after Textual exits
    transcriber.shutdown()


if __name__ == "__main__":
    main()
