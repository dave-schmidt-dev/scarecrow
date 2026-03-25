# History

## 2026-03-24

- Second-pass audit fixes: quit now drains the last recorder buffer, waits for in-flight batch work, shuts down the transcriber from the TUI path, and only then finalizes the session so transcript files are flushed and closed before exit.
- Fixed startup unwind so a failure after microphone acquisition stops the recorder and finalizes the session instead of leaking the stream or WAV handle.
- Serialized batch transcription work so overlapping 30-second ticks no longer run the shared batch model concurrently, and added a busy-path guard that keeps buffered audio for the next batch window instead of draining and dropping it.
- Reworked VAD failure handling so a transient ONNX/VAD exception resets state and retries instead of permanently killing live transcription for the rest of the session.
- Added `scripts/run_test_suite.sh` and switched hooks/docs to isolated stable pytest groups so full validation no longer depends on one long-lived pytest process that can crash under the Textual/native-extension mix.
- Added regression coverage for queue pressure, final-batch flush ordering, in-flight batch shutdown ordering, overlapping batch suppression, session-finalize failures, transcriber resource release, VAD recovery, and the correct `scarecrow.runtime.WhisperModel` patch target in tests.
- Added `BUGS.md` as the persistent bug ledger for Scarecrow and updated `README.md` with the rule that a bug is not squashed without an exact-path regression test.
- Refactored runtime ownership so `Transcriber` now owns realtime worker lifecycle, batch transcription, and callback bindings instead of splitting those responsibilities across `app.py` and `__main__.py`.
- Added `scarecrow/runtime.py` to centralize HF offline bootstrap, tqdm lock warmup, and Whisper model loading.
- Reworked live-pane rendering into a single scrollable pane with one content widget, removing the fragile RichLog clear-and-replay path.
- Added explicit error surfacing from transcription failures into the TUI and regression tests covering the UI error path.
- Added `tests/test_integration.py` to feed a real recording through the actual models with a 30-second timeout.
- Added `vulture_whitelist.py` and documented the dead-code command needed to keep dynamic Textual entry points from producing false positives.
- Added `scarecrow/env_health.py`, `scripts/repair_venv.py`, and `scripts/sync_env.py` to repair and validate the editable-install `.pth` state after environment rebuilds.
- Added `tests/test_env_health.py` to lock in the `.pth` hidden-flag remediation path and updated the README to prefer `python3 scripts/sync_env.py` over raw `uv sync`.
- Added repo-managed pre-commit and pre-push policy enforcement for required docs, HISTORY updates on code changes, BUGS regression-test references, and validation commands.
- Post-refactor audit: guarded `_on_audio` callback in recorder to prevent silent PortAudio thread death; added `threading.Lock` to `ModelManager` for batch model lazy-init thread safety; fixed pause/resume wiping live pane history; fixed int16 abs overflow in peak level; added platform guards to `env_health.py`; removed phantom `requests` dependency; rewrote stale manual scripts to use current API; cleaned up dead mock setup and inconsistent async test patterns; added fixture existence guard to integration test. Six new BUGS.md entries with matching regression tests.
