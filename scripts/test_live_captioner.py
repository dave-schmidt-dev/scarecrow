#!/usr/bin/env python3
"""Manual integration test for LiveCaptioner module.

Run with: PYTHONPATH=. .venv/bin/python scripts/test_live_captioner.py

Verifies that LiveCaptioner emits callbacks the same way the app expects.
Press Ctrl+C to stop.
"""

from __future__ import annotations

import signal
import sys

from scarecrow.live_captioner import CaptionerBindings, LiveCaptioner


def main() -> None:
    prev_text = ""

    def on_update(text: str) -> None:
        nonlocal prev_text
        common = 0
        for i in range(min(len(prev_text), len(text))):
            if prev_text[i] == text[i]:
                common = i + 1
            else:
                break
        if common < len(prev_text):
            sys.stdout.write("\b \b" * (len(prev_text) - common))
        sys.stdout.write(text[common:])
        sys.stdout.flush()
        prev_text = text

    def on_stabilized(text: str) -> None:
        nonlocal prev_text
        if prev_text:
            sys.stdout.write("\b \b" * len(prev_text))
        sys.stdout.write(text + "\n")
        sys.stdout.flush()
        prev_text = ""

    def on_error(source: str, message: str) -> None:
        print(f"\n[{source}] {message}", file=sys.stderr)

    captioner = LiveCaptioner(
        CaptionerBindings(
            on_realtime_update=on_update,
            on_realtime_stabilized=on_stabilized,
            on_error=on_error,
        )
    )

    print("Preparing...")
    captioner.prepare()
    print("Starting session...")
    captioner.begin_session()
    print("Listening... (Ctrl+C to stop)\n")

    stop = [False]
    signal.signal(signal.SIGINT, lambda *_: stop.__setitem__(0, True))

    while not stop[0]:
        captioner.tick()

    print("\nShutting down...")
    captioner.shutdown(timeout=5)
    print("Done.")


if __name__ == "__main__":
    main()
