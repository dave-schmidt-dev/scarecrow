# History

## 2026-03-27 (context injection: startup prompt, /context, /clear, rolling tail)

- App now launches in IDLE with focus on the notes input, showing a context prompt instead of auto-starting.
- Pressing Enter (empty) starts recording immediately; pressing Enter with text seeds Whisper's `initial_prompt` with the provided terms before the first batch.
- Added `/context <terms>` command: appends terms to the context list, writes a `[CONTEXT]` entry to the transcript, and updates the context display.
- Added `/clear` command: wipes all context entries and the previous-batch tail, hides the context display.
- Added `#context-display` widget between the transcript pane and notes input; shown/hidden depending on whether context is active.
- Every batch call passes `initial_prompt` built from: context entries (joined by `, `) + last 35 words of the previous batch output (rolling tail for word continuity across chunk boundaries).
- `_flush_final_batch` also passes `initial_prompt` so the final drain benefits from the same context.
- Session transcript files now begin with a `Session: YYYY-MM-DD HH:MM:SS` header line written at session-creation time.
- Removed `_auto_start` method (no longer called from anywhere); removed stale `patch.object(ScarecrowApp, "_auto_start")` calls from `tests/test_behavioral.py`.
- Updated README: startup flow, `/context`, `/clear`, context display, session header, rolling tail.

## 2026-03-27 (UI rehaul: live pane → notes pane)

- Removed `live_captioner.py` and all Apple Speech / SFSpeechRecognizer / pyobjc integration entirely.
- Removed the live pane widget (`#live-pane`, `#live-content`) and all associated app state (`_live_stable`, `_live_partial`, captioner timer, captioner callbacks).
- Replaced the live pane with a notes input pane: shortcut hints and an `Input` widget (`#note-input`).
- Added note submission logic: each note is written to the transcript pane (RichLog) and the transcript file with a wall-clock timestamp and tag prefix.
- Moved pause/quit bindings from `p`/`q` to `Ctrl+P`/`Ctrl+Q` so they do not conflict with text input in the notes pane.
- Dropped `BATCH_INTERVAL_SECONDS` from 30s to 15s for more frequent transcript updates.
- Deleted `tests/test_live_captioner.py` and removed all live-pane-specific tests from the suite.
- Deleted manual test helper scripts `scripts/test_live_captioner.py` and `scripts/test_apple_speech.py`.
- Closed BUG-20260325-live-pane-scroll-resets-at-boundary as won't fix (component removed).
- Updated README to reflect single-engine architecture, new TUI layout, and new keybindings.

### Post-rehaul refinements (2026-03-27)

- **Prefix commands replaced F1/F2/F3:** Note tags are now selected by typing a prefix in the input — `/action` or `/a` for `[ACTION]`, `/followup` or `/f` for `[FOLLOW-UP]`. Plain text (no prefix) defaults to `[NOTE]`. F1/F2/F3 bindings removed.
- **Timestamp fix:** Transcript dividers now show the start of the audio batch window (`_batch_window_start`) rather than the time Whisper finishes processing, so divider timestamps accurately reflect when the audio was captured.
- **500ms audio overlap between batch chunks:** Each batch window retains the last 500ms of audio from the previous window to reduce word drops at chunk boundaries.
- **Batch window start tracking:** Added `_batch_window_start` to record the wall-clock time at which each batch window opens, used for accurate divider timestamps.

## 2026-03-25 (live captioner debugging: four bugs fixed, one open)

- Fixed: live pane showed no output at all — `_on_realtime_update` and `_on_realtime_stabilized` were routing through `_post_to_ui` / `call_from_thread`, which Textual rejects with `RuntimeError` when called from the app's own thread (recognition callbacks fire on the main thread via `tick()` → `NSRunLoop.runUntilDate_`). Fixed by calling `_set_live_partial` / `_append_live` directly.
- Fixed: live pane stopped updating mid-session — Apple's Speech framework fires `isFinal` on silence, ending the recognition task. After `isFinal`, audio continued flowing into the dead task but no new results arrived until the 55s rotation. Fixed by detecting natural `isFinal` (via `self._request is request` closure capture) and scheduling a session restart.
- Fixed: after adding the session restart, live output showed a few words, hung, then cleared and repeated — starting a new `recognitionTaskWithRequest_resultHandler_` from inside an existing task's result handler is a reentrancy problem in Apple's Speech framework. Fixed by setting a `_needs_restart` flag in the result handler and restarting in `tick()` after the NSRunLoop pump returns, outside any callback. Split `tick()` into `_pump_runloop()` + `_tick_body()` for testability.
- Fixed: live pane filled with growing text then cleared rather than scrolling — Apple's `formattedString()` returns all text accumulated since the session started, so `_live_partial` grew to fill the entire pane before committing as one huge stable blob on `isFinal`. Fixed by incremental commit in the result handler: every `_COMMIT_THRESHOLD` (10) uncommitted words are flushed to `on_realtime_stabilized`; only `_PARTIAL_TAIL` (4) words remain as the unstable partial. Stable lines now accumulate sentence-by-sentence and the pane scrolls.
- Open (BUG-20260325-live-pane-scroll-resets-at-boundary): scroll works for ~9 lines then resets — at the pane overflow boundary, Textual's `VerticalScroll` + `Static` appears to reset virtual height rather than extending it. Logged in BUGS.md for next session.
- Added regression tests for all four fixed bugs in `tests/test_live_captioner.py` and `tests/test_behavioral.py`.

## 2026-03-25 (Phases 3-5: Apple Speech live captions)

- Replaced Whisper base.en + Silero VAD live captioning with Apple's SFSpeechRecognizer (on-device, streaming).
- Stripped Transcriber to batch-only: removed VAD state machine, realtime worker, audio queue, and accept_audio path.
- Removed onnxruntime dependency and Silero VAD ONNX model from the live path.
- ModelManager no longer loads a live Whisper model — only the batch model on first use.
- App now accepts a LiveCaptioner alongside the batch-only Transcriber, with separate callback bindings.
- Added captioner session rotation timer (every 55s) and cleanup in shutdown paths.
- Updated setup script to batch-model-only selection.
- Updated all tests, removed VAD/realtime-specific tests, added captioner shutdown coverage.

## 2026-03-25 (live captioner: Phases 1-2, segfault tolerance)

- Added `scarecrow/live_captioner.py`: streaming live captions via Apple's SFSpeechRecognizer (on-device, no network).
- Added `bin/scarecrow` wrapper script that sets PYTHONPATH to bypass the macOS UF_HIDDEN `.pth` file issue permanently.
- Updated iTerm2 profile to use the wrapper script.
- Phase 1 PoC (`scripts/test_apple_speech.py`) validated streaming word-by-word output with session rotation.
- Phase 2 module (`LiveCaptioner`) provides the same callback interface as the existing transcriber (partial + stabilized + error).
- AVAudioEngine tap runs on the main thread (required by macOS); Textual's event loop pumps the run loop.
- Added `tests/test_live_captioner.py` with lifecycle, binding, and error tests.
- Phases 3-5 (app integration, Whisper live removal, docs) are planned in `PLAN-live-captioner.md`.
- Test suite runner now tolerates exit code 139 (SIGSEGV during native-extension teardown) since the segfault occurs after pytest reports success.

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
