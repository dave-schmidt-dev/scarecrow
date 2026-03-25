# History

## 2026-03-25 (audio drop error handling)

- Changed audio drop error to report once per session instead of on every queue full/not-full cycle.
- Audio drop messages now appear in the status bar only, not the transcript pane. Other transcription errors still go to the transcript pane.

## 2026-03-25 (launch alias and iTerm2 profile fix)

- Changed iTerm2 profile and shell alias to call `.venv/bin/scarecrow` directly instead of `uv run`, which re-triggers the macOS `UF_HIDDEN` flag on the editable-install `.pth` file every launch.
- Added `chflags nohidden` safety net to the iTerm2 profile command.
- Updated README to document the `uv run` avoidance and the direct venv binary approach.

## 2026-03-25 (audit round 2: batch capture, timeout hardening, cleanup)

- Fixed in-flight batch worker text being silently lost on quit by capturing future return values in `_wait_for_batch_workers` and writing them to the session transcript before finalize.
- Fixed type annotation for `_batch_futures` (`Future[None]` → `Future[str | None]`).
- Added explicit batch executor shutdown in the normal cleanup path to prevent atexit hangs.
- Added 5-second timeout to realtime transcriber worker shutdown (was `None`, blocking indefinitely).
- Replaced private `_worker` attribute access on Transcriber with a public `has_active_worker` property.
- Made `Session.finalize()` fully idempotent under KeyboardInterrupt by tracking finalized state separately from the file handle.
- Updated policy checker to reject "manual only" regression test entries in BUGS.md.
- Marked BUG-20260324-quit-drops-final-batch as squashed; updated stale-manual-scripts entry.
- Added regression tests for: in-flight batch text capture, session finalize under interrupt, transcriber shutdown timeout, batch executor cleanup, on_unmount safety.

## 2026-03-24 (shutdown and setup follow-up)

- Routed Ctrl+C cleanup through `app.cleanup_after_exit()` so the final buffered batch is flushed to the session transcript before shutdown completes.
- Hardened shutdown after batch-worker timeout by abandoning the executor, ignoring late batch callbacks, skipping the final flush, and continuing shutdown.
- Added automated regression coverage for `scripts/setup.py` in `tests/test_setup.py`, and included that file in `scripts/run_test_suite.sh`.
- Reconciled `README.md` and `BUGS.md` with the new shutdown and setup behavior.

## 2026-03-24

- Hardened the hook test runner by sanitizing the pytest environment in `scripts/run_test_suite.sh`, isolating every test file in its own subprocess, splitting `tests/test_behavioral.py` to one test node per process, and routing each invocation through `scripts/run_pytest_file.py` so successful test runs exit cleanly without hitting the native interpreter-teardown crash path seen under pre-commit/pre-push shells.
- Fixed a shutdown timeout race in `Transcriber.shutdown()` so timed-out joins no longer clear VAD/model state out from under a still-running worker, and aligned the real integration test with the blocking shutdown path used by the app.
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
