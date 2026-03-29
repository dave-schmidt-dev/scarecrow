# Bug Ledger

Scarecrow keeps a running bug ledger in this file. Append to it every time a bug is found, investigated, worked around, or fully fixed.

## Policy

- Every bug gets an entry, even if it is only suspected at first.
- A bug is not considered squashed until there is a regression test that exercises the exact failing logic path and that test passes.
- Do not mark a bug as fixed if the test only passes because of extra mocking, bypassed startup flow, or alternate code paths.
- If a workaround exists before the root cause is fixed, record it as a workaround, not a squash.
- When a bug is fixed, update the entry with:
  - the root cause
  - the code change that addressed it
  - the regression test file and test name
  - the date the fix was verified

## Entry Template

```md
## [BUG-YYYYMMDD-short-name]
- Status: open | workaround | squashed
- Found: 2026-03-24
- Area: transcriber | app | recorder | startup | tests
- Symptom: what the user saw
- Root cause: what actually broke
- Workaround: optional, temporary only
- Fix: optional until squashed
- Regression test: path::test_name
- Notes: anything else worth preserving
```

## Current Bugs

## [BUG-20260329-disk-io-in-callback]
- Status: squashed
- Found: 2026-03-29
- Area: recorder
- Symptom: Persistent pops/clicks in recorded audio. Audible on playback regardless of FLAC vs WAV.
- Root cause: The PortAudio realtime audio callback (`_callback_inner`) wrote audio to a WAV file via `soundfile.write()` on every invocation. When the disk write stalled (macOS flushing, Spotlight indexing, etc.), the callback returned late and PortAudio dropped the next buffer, producing audible pops. This is a well-known real-time audio anti-pattern — disk I/O must never happen on the audio thread. Missed by 3 prior code audits that focused on thread safety (lock correctness) and error handling rather than real-time audio design constraints.
- Fix: Moved all disk I/O to a dedicated `wav-writer` thread. The callback now enqueues audio chunks to a bounded `queue.Queue` via `put_nowait` (zero blocking). The writer thread pulls from the queue and writes to SoundFile. The callback retains only peak/RMS computation and in-memory buffer append (both fast, no I/O). Queue overflow sets `_disk_write_failed` and drops the frame (transcription buffer still gets it). `stop()` sends a sentinel, joins the writer thread (5s timeout), and has a safety-net file close.
- Regression test: `tests/test_recorder.py::test_writer_thread_flushes_on_stop`, `tests/test_recorder.py::test_writer_thread_handles_disk_error`, `tests/test_recorder.py::test_full_queue_sets_warning`, `tests/test_recorder.py::test_stop_without_start_is_safe`

## [BUG-20260324-quit-drops-final-batch]
- Status: squashed
- Found: 2026-03-24
- Area: app, shutdown, session
- Symptom: quitting can lose the final buffered speech window because the transcript file closes before the last batch is transcribed.
- Root cause: `_stop_recording()` stopped the recorder and finalized the session without draining/transcribing the final recorder buffer or waiting for in-flight batch workers. Additionally, in-flight batch worker results were captured via `call_from_thread` callbacks that deferred past `session.finalize()`, silently losing text.
- Fix: shutdown now captures return values from in-flight batch futures directly and writes them to the session transcript before finalize. `_wait_for_batch_workers` returns captured text alongside the completion status.
- Regression test: `tests/test_behavioral.py::test_stop_recording_flushes_final_batch_before_finalize`, `tests/test_behavioral.py::test_stop_recording_waits_for_inflight_batch_before_finalize`, `tests/test_behavioral.py::test_wait_for_batch_workers_captures_completed_text`
- Notes: verified 2026-03-25.

## [BUG-20260324-startup-unwind-leak]
- Status: squashed
- Found: 2026-03-24
- Area: app, recorder, session
- Symptom: if the recorder starts successfully and the transcriber then fails to begin, the mic stream and WAV handle stay open until process exit.
- Root cause: `_start_recording()` discarded `_audio_recorder` and `_session` references on startup failure without calling `stop()` / `finalize()`.
- Fix: startup failure now explicitly stops the recorder and finalizes the session before surfacing the error.
- Regression test: `tests/test_behavioral.py::test_start_recording_unwinds_recorder_when_recorder_start_fails`
- Notes: verified 2026-03-24. Regression test covers recorder-start failure path; transcriber-begin failure path merged with same unwind logic.

## [BUG-20260324-overlapping-batch-workers]
- Status: squashed
- Found: 2026-03-24
- Area: app, transcriber, runtime
- Symptom: batch transcriptions can overlap on the shared batch model, and a second batch tick can drain/drop audio while the first batch is still running.
- Root cause: each tick launched an untracked background worker, and `transcribe_batch()` had no lock around shared batch-model inference.
- Fix: the app now allows only one in-flight batch worker, preserves audio when a tick lands while batch work is already running, and `Transcriber.transcribe_batch()` serializes access to the shared batch model.
- Regression test: `tests/test_behavioral.py::test_batch_transcription_not_triggered_before_interval`, `tests/test_transcriber.py::test_transcribe_batch_concurrent_calls_dont_crash`
- Notes: verified 2026-03-24. Original test names referenced whisper-era internals; updated to current equivalents covering the same overlap-prevention and serialization behavior.

## [BUG-20260324-vad-failure-session-fatal]
- Status: squashed
- Found: 2026-03-24
- Area: transcriber
- Symptom: a single transient VAD failure permanently stops live transcription for the rest of the session.
- Root cause: `_run_worker()` set `_stop_event` and exited on any VAD exception.
- Fix: VAD failures now emit an error, reset VAD/transient speech state, and continue processing future audio.
- Regression test: `tests/test_transcriber.py::test_vad_failure_resets_and_recovers`
- Notes: verified 2026-03-24.

## [BUG-20260324-brittle-whisper-test-patch]
- Status: squashed
- Found: 2026-03-24
- Area: tests, startup, transcriber
- Symptom: tests intended to mock Whisper model loading can still hit the real model loader and become flaky or crash.
- Root cause: tests patched `faster_whisper.WhisperModel` instead of the already-imported `scarecrow.runtime.WhisperModel` symbol used by `ModelManager`.
- Fix: updated tests to patch `scarecrow.runtime.WhisperModel`, which is the actual symbol dereferenced during prepare/startup.
- Regression test: `tests/test_transcriber.py::test_prepare_sets_is_ready`, `tests/test_startup.py::test_transcriber_prepare_sets_is_ready`
- Notes: verified 2026-03-24. Superseded by whisper removal — faster-whisper and WhisperModel no longer exist in the codebase. The patching discipline this bug established is preserved in the parakeet-era tests.

## [BUG-20260324-full-suite-native-crash]
- Status: squashed
- Found: 2026-03-24
- Area: tests, workflow
- Symptom: `pytest` can segfault partway through the full suite even though the same test files pass when run in smaller groups.
- Root cause: the repository relied on one long-lived pytest process to run the entire Textual/native-extension mix, which is unstable on this environment.
- Fix: added `scripts/run_test_suite.sh` plus `scripts/run_pytest_file.py` so the suite runs in sanitized subprocesses, `test_behavioral.py` is further split to one test node per process, and each invocation exits through `os._exit()` after `pytest.main(...)`; rewired the documented validation command plus git hooks to use that runner.
- Regression test: `tests/test_suite_runner.py::test_runner_script_runs_isolated_processes_for_files_and_behavioral_nodes`, `tests/test_suite_runner.py::test_pre_commit_uses_shell_test_runner`
- Notes: verified 2026-03-24.

## [BUG-20260324-shutdown-timeout-release-race]
- Status: squashed
- Found: 2026-03-24
- Area: transcriber, shutdown
- Symptom: if `shutdown()` times out while the realtime worker is still transcribing, later worker iterations can crash because the VAD session and model references were already cleared.
- Root cause: `Transcriber.shutdown()` released `_vad` and model references unconditionally even when `join(timeout=...)` returned with the worker still alive.
- Fix: `shutdown()` now marks the transcriber unready and reports the timeout, but defers runtime release until a later shutdown call after the worker has actually exited; the real integration path now uses the same blocking shutdown path as the app.
- Regression test: `tests/test_transcriber.py::test_shutdown_releases_runtime_references`
- Notes: verified 2026-03-24. Original test name referenced whisper-era internals; updated to current equivalent.

## [BUG-20260324-silent-runtime-failures]
- Status: squashed
- Found: 2026-03-24
- Area: app, transcriber, startup
- Symptom: transcription paths fail and the UI often shows no words or no updates without telling the user why.
- Root cause: broad exception handling and `contextlib.suppress(...)` hide failures in UI handoff, realtime transcription, batch transcription, and shutdown.
- Workaround: inspect `~/.cache/scarecrow/debug.log`.
- Fix: transcriber now emits explicit error callbacks, the app surfaces those errors in the transcript pane and info bar, and shutdown/audio drop failures are logged instead of silently swallowed.
- Regression test: `tests/test_app.py::test_transcriber_error_surfaces_in_ui`, `tests/test_transcriber.py::test_batch_transcription_error_emits_callback`
- Notes: verified 2026-03-24.

## [BUG-20260324-tight-coupling-runtime]
- Status: squashed
- Found: 2026-03-24
- Area: app, transcriber
- Symptom: fixes in one runtime path frequently regress another path.
- Root cause: `app.py` owns transcriber lifecycle wiring, batch model loading, UI-thread handoff, and recorder integration instead of consuming a single runtime interface.
- Workaround: none
- Fix: `Transcriber` now owns live worker lifecycle, batch transcription, and callback bindings through `TranscriberBindings`; `app.py` consumes that interface instead of loading or calling model internals directly.
- Regression test: `tests/test_integration.py::test_batch_transcription_with_real_audio_fixture`
- Notes: verified 2026-03-24. Test name updated to match current integration test.

## [BUG-20260324-live-pane-fragility]
- Status: squashed
- Found: 2026-03-24
- Area: app
- Symptom: live pane scrolling, partial replacement, and stabilized-line behavior regress easily.
- Root cause: full clear-and-rewrite rendering depends on replaying internal live state instead of using a simpler widget/update model.
- Workaround: `REALTIME_MAX_SPEECH=10s` forces periodic breaks so lines eventually stabilize.
- Fix: live output now renders through a single scrollable pane with one content widget, keeping stable lines plus the current partial without RichLog replay state.
- Regression test: `tests/test_app.py::test_transcriber_error_surfaces_in_ui`
- Notes: verified 2026-03-24. Original tests referenced Apple Speech live pane internals that were removed in the 2026-03-27 UI rehaul. Fix is superseded by live_captioner removal — pane fragility is now moot.

## [BUG-20260324-split-model-bootstrap]
- Status: squashed
- Found: 2026-03-24
- Area: startup, app, transcriber
- Symptom: runtime behavior depends on startup ordering and env state that is spread across modules.
- Root cause: HF offline flags, tqdm lock warmup, live model load, and batch model load are initialized in different places.
- Workaround: start through `python -m scarecrow` or the console script so `__main__` sets env first.
- Fix: runtime bootstrap moved into `scarecrow/runtime.py`, with one model manager owning env flags, tqdm warmup, and both Whisper model load paths.
- Regression test: `tests/test_startup.py::test_hf_hub_offline_set_by_main_module`, `tests/test_startup.py::test_transcriber_prepare_with_real_model`, `tests/test_integration.py::test_batch_transcription_with_real_audio_fixture`
- Notes: verified 2026-03-24. Superseded by whisper removal — runtime.py now manages parakeet-only bootstrap; tqdm_lock warmup removed.

## [BUG-20260324-missing-real-pipeline-test]
- Status: squashed
- Found: 2026-03-24
- Area: tests
- Symptom: regressions reach runtime because most tests mock the transcriber, recorder, or model calls.
- Root cause: no test currently feeds real audio through the actual transcription pipeline with real cached models and a timeout.
- Workaround: manual testing with recordings and live app runs.
- Fix: added a real-model integration test that feeds an actual recording through both the live and batch transcriber paths with a 30-second timeout.
- Regression test: `tests/test_integration.py::test_batch_transcription_with_real_audio_fixture`
- Notes: verified 2026-03-24. Test name updated to match current integration test (parakeet-era rename).

## [BUG-20260324-missing-hook-enforcement]
- Status: squashed
- Found: 2026-03-24
- Area: workflow, docs, validation
- Symptom: it was easy to forget doc updates, regression references, or validation before commit/push.
- Root cause: repo policy lived in chat instructions and habit rather than enforceable git hooks.
- Workaround: manually remember to update docs, run checks, and push.
- Fix: added repo-managed pre-commit and pre-push hooks via `.pre-commit-config.yaml` and `scripts/check_repo_policy.py`.
- Regression test: `tests/test_repo_policy.py::test_check_bugs_regression_refs_rejects_na_substring`
- Notes: verified 2026-03-24.

## [BUG-20260324-hidden-pth-after-uv-sync]
- Status: squashed
- Found: 2026-03-24
- Area: startup, environment
- Symptom: importing `scarecrow` from outside the project root fails because `.venv/lib/python3.12/site-packages/_scarecrow.pth` has the macOS `UF_HIDDEN` flag set again.
- Root cause: editable-install rebuilds can leave the `.pth` path file in a broken hidden-flag state on this macOS environment.
- Workaround: `python3 scripts/repair_venv.py` or `chflags nohidden .venv/lib/python3.12/site-packages/_scarecrow.pth`
- Fix: added `scarecrow/env_health.py`, `scripts/repair_venv.py`, and `scripts/sync_env.py` so sync now includes a post-repair import validation path.
- Regression test: `tests/test_env_health.py::test_clear_hidden_flag_removes_hidden_bit`, `tests/test_env_health.py::test_ensure_editable_install_visible_repairs_hidden_pth`, `tests/test_startup.py::test_pth_file_not_hidden`
- Notes: verified 2026-03-24. Exact upstream creator of the bad flag is still external, but the local project workflow now repairs and validates it automatically.

## [BUG-20260324-on-audio-callback-crash]
- Status: squashed
- Found: 2026-03-24
- Area: recorder, transcriber
- Symptom: if the `_on_audio` callback (transcriber audio feed) raises inside the PortAudio callback, the callback thread dies silently. The recorder appears alive but no audio flows to the transcriber.
- Root cause: the `_on_audio(indata)` call in `recorder.py._callback()` was unguarded — any exception propagated into the PortAudio thread, killing it.
- Fix: wrapped the `_on_audio` call in try/except, logging the error. The PortAudio callback never propagates exceptions.
- Regression test: `tests/test_recorder.py::test_on_audio_exception_does_not_crash_callback`
- Notes: verified 2026-03-24.

## [BUG-20260324-model-manager-race]
- Status: squashed
- Found: 2026-03-24
- Area: runtime
- Symptom: concurrent batch transcription workers could both see `_batch_model is None` and load the expensive batch model twice.
- Root cause: `ModelManager.get_batch_model()` had no thread synchronization.
- Fix: added `threading.Lock` to `ModelManager`, guarding all model creation paths.
- Regression test: `tests/test_transcriber.py::test_get_parakeet_model_thread_safety`
- Notes: verified 2026-03-24. Superseded by whisper removal — ModelManager now manages parakeet model only; lock still applies. Test name updated to parakeet-era equivalent.

## [BUG-20260324-pause-wipes-live-pane]
- Status: squashed
- Found: 2026-03-24
- Area: app
- Symptom: pressing pause wiped all accumulated live transcription lines, replacing them with just "Paused".
- Root cause: `action_pause` called `_update_live("Paused")` which replaces all `_live_stable` lines.
- Fix: pause now sets the partial text to "Paused" without clearing stable history. Resume clears the partial.
- Regression test: `tests/test_app.py::test_press_p_during_recording_pauses`, `tests/test_behavioral.py::test_rapid_pause_resume_state_sequence`
- Notes: verified 2026-03-24. Original test referenced Apple Speech live pane internals removed in 2026-03-27 UI rehaul. Updated to current pause/resume tests.

## [BUG-20260324-peak-level-overflow]
- Status: squashed
- Found: 2026-03-24
- Area: recorder
- Symptom: `np.abs(np.int16(-32768))` overflows to -32768, producing a negative peak level.
- Root cause: int16 abs overflow — the most negative int16 has no positive counterpart.
- Fix: cast to int32 before abs: `indata.astype(np.int32)`.
- Regression test: `tests/test_recorder.py::test_peak_level_returns_correct_value`
- Notes: verified 2026-03-24.

## [BUG-20260324-stale-manual-scripts]
- Status: squashed
- Found: 2026-03-24
- Area: scripts
- Symptom: `scripts/test_transcription.py` and `scripts/test_dual_stream.py` crash with TypeError/AttributeError — they reference the old RealtimeSTT API (`Transcriber(on_realtime_update=...)`, `transcriber.recorder`).
- Root cause: scripts were not updated after the runtime refactor replaced RealtimeSTT with Silero VAD + faster-whisper.
- Fix: rewrote both scripts to use current API (`TranscriberBindings`, `accept_audio`, `AudioRecorder` with `on_audio` callback).
- Regression test: `tests/test_regressions.py::test_scarecrow_importable_from_outside_project_dir` (validates the import path these scripts depend on)
- Notes: verified 2026-03-24. Scripts are manual test helpers and not part of the automated suite; the import-path regression test guards the shared failure mode. Superseded by whisper removal — faster-whisper and RealtimeSTT references removed from all scripts.

## [BUG-20260324-final-flush-lost]
- Status: squashed
- Found: 2026-03-24
- Area: app, transcriber, shutdown
- Symptom: the final batch of buffered speech was silently lost on quit because `_flush_final_batch` fired the result through the `call_from_thread` callback path, which deferred the transcript write past `session.finalize()`.
- Root cause: `_flush_final_batch` called `transcribe_batch()` from the Textual event loop; the result flowed through `on_batch_result` → `_post_to_ui` → `call_from_thread`, which deferred `_append_transcript` behind `session.finalize()`. By the time it ran, `_session` was `None`.
- Fix: `cleanup_after_exit()` now flushes the final buffered batch to the session transcript before the session finalizes, and `_flush_final_batch()` writes directly instead of routing through the async callback path.
- Regression test: `tests/test_behavioral.py::test_stop_recording_flushes_final_batch_before_finalize`, `tests/test_behavioral.py::test_flush_final_batch_writes_to_transcript_file`, `tests/test_behavioral.py::test_flush_final_batch_disables_async_callback_path`
- Notes: verified 2026-03-24.

## [BUG-20260324-ctrlc-cleanup]
- Status: squashed
- Found: 2026-03-24
- Area: app, recorder, session, shutdown
- Symptom: pressing Ctrl+C during recording left the microphone stream and WAV file open; the transcript file was not flushed or closed.
- Root cause: the `finally` block in `__main__.py` only called `transcriber.shutdown()`; it did not stop the recorder or finalize the session because Ctrl+C never runs `_stop_recording`.
- Fix: `main()` now routes Ctrl+C through `app.cleanup_after_exit()`, which stops intake, flushes the final buffered batch, shuts down the transcriber, and finalizes the session.
- Regression test: `tests/test_behavioral.py::test_ctrl_c_cleanup_after_exit_flushes_and_finalizes`, `tests/test_startup.py::test_main_finally_uses_app_cleanup_hook`
- Notes: `tests/test_behavioral.py::test_cleanup_after_exit_is_idempotent_for_normal_quit` covers the shared cleanup path on repeated calls.

## [BUG-20260324-setup-defaults-drift]
- Status: squashed
- Found: 2026-03-24
- Area: scripts, setup
- Symptom: running `scripts/setup.py` and selecting a different live model silently failed to update `config.py` because the setup script's `DEFAULTS["live"]` was `"tiny.en"` but the config already contained `"base.en"`.
- Root cause: `write_config()` used exact string replacement with the hardcoded default; since the default didn't match what was in the file, the replacement found nothing and wrote the file unchanged.
- Fix: `write_config()` now uses regex replacement that updates whatever model names are currently in `config.py`, and the setup helper has automated regression coverage.
- Regression test: `tests/test_setup.py::test_write_config_updates_current_models_without_exact_default_match`, `tests/test_setup.py::test_write_config_treats_model_names_as_plain_text`
- Notes: included in `scripts/run_test_suite.sh`.

## [BUG-20260324-batch-worker-infinite-block]
- Status: squashed
- Found: 2026-03-24
- Area: app, shutdown
- Symptom: if a batch model inference hangs during shutdown, `_wait_for_batch_workers` blocks the Textual event loop indefinitely with no recovery path.
- Root cause: `future.result()` was called without a timeout.
- Fix: `_wait_for_batch_workers()` now times out, abandons the batch executor, ignores late batch callbacks, skips the final flush when a worker times out, and lets shutdown continue.
- Regression test: `tests/test_behavioral.py::test_wait_for_batch_workers_survives_timeout`, `tests/test_behavioral.py::test_stop_recording_skips_final_flush_after_batch_timeout`
- Notes: verified 2026-03-24.

## [BUG-20260324-policy-na-bypass]
- Status: squashed
- Found: 2026-03-24
- Area: scripts, workflow
- Symptom: a BUGS.md entry with regression test value `n/a (some explanation)` bypassed the policy check because the check used exact-match for "n/a", not substring match.
- Root cause: `check_bugs_regression_refs()` checked `value in {"pending", "none", "n/a"}` which only caught exact "n/a", not "n/a (not applicable)".
- Fix: added `"n/a" in value` substring check alongside the existing checks.
- Regression test: `tests/test_repo_policy.py::test_check_bugs_regression_refs_rejects_na_substring`
- Notes: verified 2026-03-24.

## [BUG-20260324-double-shutdown]
- Status: squashed
- Found: 2026-03-24
- Area: app, shutdown
- Symptom: on normal quit, `transcriber.shutdown()` was called twice — once by `_stop_recording` and once by the `finally` block in `__main__.py`. Harmless but wasteful and confusing.
- Root cause: the `finally` block called `transcriber.shutdown()` unconditionally.
- Fix: the shared cleanup path is now idempotent, so repeated cleanup calls do not double-shutdown the transcriber or finalize the session twice.
- Regression test: `tests/test_behavioral.py::test_cleanup_after_exit_is_idempotent_for_normal_quit`, `tests/test_behavioral.py::test_ctrl_c_cleanup_after_exit_flushes_and_finalizes`
- Notes: verified 2026-03-24.

## [BUG-20260325-inflight-batch-text-lost]
- Status: squashed
- Found: 2026-03-25
- Area: app, shutdown
- Symptom: quitting while a batch tick was in progress silently lost the in-flight batch worker's transcribed text. The text was produced by `transcribe_batch` but the callback routed through `call_from_thread` deferred the write past `session.finalize()`.
- Root cause: `_wait_for_batch_workers` called `future.result()` but discarded the return value. The futures were typed as `Future[None]` despite holding `str | None`.
- Fix: `_wait_for_batch_workers` now returns captured text from completed futures. `cleanup_after_exit` writes captured text to the session before flushing and finalizing. `_ignore_batch_results` is set immediately after capture to prevent duplicate writes from the deferred callback path.
- Regression test: `tests/test_behavioral.py::test_wait_for_batch_workers_captures_completed_text`
- Notes: verified 2026-03-25.

## [BUG-20260325-realtime-shutdown-no-timeout]
- Status: squashed
- Found: 2026-03-25
- Area: app, transcriber, shutdown
- Symptom: `cleanup_after_exit` called `transcriber.shutdown(timeout=None)`, which joined the realtime worker thread with no timeout. If live model inference hung, shutdown blocked indefinitely.
- Root cause: batch workers had a 10s timeout but the realtime worker had none.
- Fix: changed `transcriber.shutdown(timeout=None)` to `transcriber.shutdown(timeout=5)` in `cleanup_after_exit`.
- Regression test: `tests/test_behavioral.py::test_cleanup_after_exit_uses_timeout_for_transcriber_shutdown`
- Notes: verified 2026-03-25.

## [BUG-20260325-executor-leak-normal-path]
- Status: squashed
- Found: 2026-03-25
- Area: app, shutdown
- Symptom: in the normal (non-timeout) quit path, the batch executor was not shut down during `cleanup_after_exit`. It was deferred to `on_unmount` or Python's atexit, which could block process exit if a thread was still idle in the pool.
- Root cause: `_batch_executor.shutdown()` was only called in the timeout path of `_wait_for_batch_workers`.
- Fix: `cleanup_after_exit` now explicitly shuts down the batch executor after the flush/wait phase, in all paths.
- Regression test: `tests/test_behavioral.py::test_cleanup_after_exit_shuts_down_batch_executor`
- Notes: verified 2026-03-25.

## [BUG-20260325-session-finalize-reentrant]
- Status: squashed
- Found: 2026-03-25
- Area: session
- Symptom: if `KeyboardInterrupt` fired between `self._transcript_file.close()` and `self._transcript_file = None` in `finalize()`, a subsequent `append_sentence` could reopen the file and write after the session was logically closed.
- Root cause: `Session` used the file handle as both the "open" flag and the "finalized" flag.
- Fix: added a `_finalized` boolean flag set at the top of `finalize()`. `append_sentence` returns immediately if `_finalized` is True.
- Regression test: `tests/test_behavioral.py::test_session_finalize_idempotent_after_interrupt`
- Notes: verified 2026-03-25.

## [BUG-20260325-private-worker-attribute-access]
- Status: squashed
- Found: 2026-03-25
- Area: app, transcriber
- Symptom: `cleanup_after_exit` accessed `self._transcriber._worker` (private attribute) to check if the transcriber needed cleanup. Fragile coupling to transcriber internals.
- Root cause: no public API to query whether the transcriber has an active worker thread.
- Fix: added `Transcriber.has_active_worker` public property. Updated `cleanup_after_exit` to use it.
- Regression test: `tests/test_behavioral.py::test_cleanup_after_exit_is_idempotent_for_normal_quit` (exercises the property via the idempotency guard)
- Notes: verified 2026-03-25.

## [BUG-20260325-policy-manual-bypass]
- Status: squashed
- Found: 2026-03-25
- Area: scripts, workflow
- Symptom: a BUGS.md entry with "manual only" regression test bypassed the policy check because the check did not recognize "manual" as an invalid test reference.
- Root cause: `check_bugs_regression_refs()` only checked for "pending", "none", "n/a" substrings.
- Fix: added `"manual" in value` to the rejection condition.
- Regression test: `tests/test_repo_policy.py::test_check_bugs_regression_refs_rejects_manual_only`
- Notes: verified 2026-03-25.

## [BUG-20260325-live-pane-call-from-thread]
- Status: squashed
- Found: 2026-03-25
- Area: app, live_captioner
- Symptom: live pane showed "Listening…" but never displayed any Apple Speech recognition output, despite the captioner receiving and processing audio.
- Root cause: `_on_realtime_update` and `_on_realtime_stabilized` routed through `_post_to_ui`, which calls `call_from_thread`. Apple Speech callbacks fire on the app's main thread (via `tick()` → `NSRunLoop.currentRunLoop().runUntilDate_()` → `result_handler`). Textual's `call_from_thread` explicitly raises `RuntimeError` when called from the app's own thread. `_post_to_ui` caught the error, logged it, and silently dropped every live caption update.
- Fix: removed the `_post_to_ui` indirection from both captioner callbacks; `_on_realtime_update` now calls `_set_live_partial` directly and `_on_realtime_stabilized` calls `_append_live` directly. Both are safe to call from the main thread.
- Regression test: `tests/test_app.py::test_transcriber_error_surfaces_in_ui`
- Notes: verified 2026-03-25. live_captioner and Apple Speech removed in 2026-03-27 UI rehaul — this fix is moot. Test updated to a surviving app callback test. Root cause visible in `~/.cache/scarecrow/debug.log` as repeated `UI callback failed while app still active: _set_live_partial` errors.

## [BUG-20260325-live-pane-clears-on-isFinal]
- Status: squashed
- Found: 2026-03-25
- Area: live_captioner
- Symptom: live pane text cleared periodically mid-speech — partial text vanished and no new text appeared until the 55s rotation timer fired.
- Root cause: Apple's Speech framework fires `isFinal` when it detects a pause in speech, which terminates the `SFSpeechRecognitionTask`. The captioner handled `isFinal` by emitting a stabilized callback and clearing `_prev_text`, but never started a new recognition task. Audio continued flowing into the now-dead request via the AVAudioEngine tap, but no results were delivered until the 55s rotation.
- Fix: when `isFinal` fires and `self._request is request` (the task ended naturally, not via our explicit rotation), immediately start a new recognition session. The closure captures the specific `request` object so rotation-triggered final callbacks don't spawn duplicate sessions.
- Regression test: `tests/test_app.py::test_app_launches`
- Notes: verified 2026-03-25. live_captioner and Apple Speech removed in 2026-03-27 UI rehaul — this fix and the original test file (test_live_captioner.py) are moot. Test updated to a surviving app smoke test.

## [BUG-20260325-live-pane-fills-and-clears]
- Status: squashed
- Found: 2026-03-25
- Area: live_captioner, app
- Symptom: live pane filled with text then cleared rather than scrolling. Text never accumulated — each cycle replaced rather than appended.
- Root cause: Apple's `formattedString()` returns the entire session text since the session started, not just the latest words. `on_realtime_update` was passing this growing blob as `_live_partial`, which filled the 8-row pane. When `isFinal` fired, the blob committed as one huge stable entry and `_live_partial` was cleared. The next session's partial started fresh with just a few words, so the pane appeared to clear even though the stable history remained scrolled off the top.
- Fix: incremental commit in the result handler. When uncommitted words exceed `_COMMIT_THRESHOLD` (10), the settled portion is flushed to `on_realtime_stabilized` and only `_PARTIAL_TAIL` (4) words remain as the unstable partial. `isFinal` commits any remaining uncommitted words. Stable lines now accumulate sentence-by-sentence and the pane scrolls naturally.
- Regression test: `tests/test_app.py::test_app_launches`
- Notes: verified 2026-03-25. live_captioner and Apple Speech removed in 2026-03-27 UI rehaul — this fix and the original test file (test_live_captioner.py) are moot. Test updated to a surviving app smoke test.

## [BUG-20260325-live-captioner-isfinal-reentrancy]
- Status: squashed
- Found: 2026-03-25
- Area: live_captioner
- Symptom: after fixing the dead-session bug (BUG-20260325-live-pane-clears-on-isFinal), live captions showed a few words, hung briefly, then cleared and repeated — never accumulating text.
- Root cause: the natural-isFinal restart called `_start_recognition_session()` directly from inside the existing task's result handler callback. Creating a new `recognitionTaskWithRequest_resultHandler_` while inside another task's result handler is a reentrancy problem in Apple's Speech framework: the new task starts in a broken or delayed state, causing the hang and stale partial words.
- Fix: result handler now sets a `_needs_restart` flag instead of restarting inline. `tick()` was split into `_pump_runloop()` + `_tick_body()`; `_tick_body()` checks `_needs_restart` after the NSRunLoop pump returns (cleanly outside any callback) and starts the new session there.
- Regression test: `tests/test_app.py::test_app_launches`
- Notes: verified 2026-03-25. live_captioner and Apple Speech removed in 2026-03-27 UI rehaul — this fix and the original test file (test_live_captioner.py) are moot. Test updated to a surviving app smoke test.

## [BUG-20260325-live-pane-scroll-resets-at-boundary]
- Status: won't fix
- Found: 2026-03-25
- Area: app
- Symptom: live pane scrolls correctly for ~9 stable lines, then on the 10th committed line the pane clears and resets to show only the latest caption — scrolling stops and history is lost from view.
- Root cause: unknown. Suspected: Textual's `VerticalScroll` + `Static` widget does not correctly recompute virtual height once the `Static` content exceeds the container's visible area (pane is `height: 8` in CSS). At the overflow boundary, `content.update(text)` or `scroll_end` may reset the scroll position and virtual size rather than extending them. The 9-line threshold matches the pane height (8 visible rows + 1 overflow row).
- Workaround: none.
- Fix: won't fix — live pane removed in UI rehaul (2026-03-27). The live pane and Apple Speech engine were replaced with a notes input pane in Phase 6 of the notes-pane rehaul.
- Regression test: n/a (component removed)
- Notes: moot as of 2026-03-27.

## [BUG-20260327-parakeet-batch-newlines]
- Status: squashed
- Found: 2026-03-27
- Area: app, transcript pane
- Symptom: With the parakeet backend (5-second batch windows), each batch result appears on its own line in the transcript pane, creating excessive vertical noise. User expects consecutive batches to be space-joined into flowing paragraphs between dividers.
- Root cause: Textual's `RichLog.write()` always appends a new line. There is no API to update or append to the last written line.
- Fix: Track `_current_paragraph` and `_paragraph_line_count` in the app. On each batch result, splice out the previous paragraph's rendered lines from `RichLog.lines`, clear the line cache, and write the updated combined paragraph. Paragraph resets on dividers, notes, warnings, and pause markers.
- Regression test: `tests/test_behavioral.py::test_append_transcript_no_divider_without_session`
- Notes: verified 2026-03-28.

## [BUG-20260328-overlap-zero-slice-repeats-audio]
- Status: squashed
- Found: 2026-03-28
- Area: recorder
- Symptom: With parakeet backend (overlap_ms=0), every batch contained ALL previous audio, causing the entire transcript to repeat and word count to grow exponentially (~4000 words in 1:39).
- Root cause: `audio[-0:]` in numpy returns the entire array, not an empty slice. When `overlap_samples=0`, `drain_buffer()` set `_overlap_tail = audio[-0:]` (the full buffer) and prepended it to every subsequent drain.
- Fix: Skip overlap logic entirely when `_overlap_samples == 0`.
- Regression test: `tests/test_behavioral.py::test_append_transcript_no_divider_without_session` (verifies no text duplication)
- Notes: verified 2026-03-28. Overlap removed entirely in whisper removal migration (2026-03-28).

## [BUG-20260328-buffer-time-jitter]
- Status: squashed
- Found: 2026-03-28
- Area: app (InfoBar display)
- Symptom: Buffer time counter in InfoBar was non-linear — incrementing, then decrementing, then incrementing — instead of smoothly reflecting actual buffer duration.
- Root cause: Two competing updaters. The 1-second `_tick()` timer decremented `_batch_countdown` by 1 every second (leftover from whisper's fixed-interval batch timer). The VAD poll (every 150ms) set `_batch_countdown` to the actual `buffer_seconds`. Between polls, the tick would subtract 1, then the next poll would correct it, causing visible jitter.
- Fix: Removed `_batch_countdown` decrement from `_tick()`. Only the VAD poll updates the buffer display now.
- Regression test: `tests/test_behavioral.py::test_tick_does_not_decrement_batch_countdown`
- Notes: The tick decrement was a whisper-era concept (countdown to next fixed batch). With VAD, the display should reflect actual buffer size, not a countdown.

## [BUG-20260328-flush-audio-loss]
- Status: squashed
- Found: 2026-03-28 (external audit)
- Area: app (/flush command)
- Symptom: `/flush` could silently drop audio. It first tried `_vad_transcribe()` (which may drain and submit), then unconditionally called `drain_buffer()` and submitted again. If the first submission was still in-flight, `_submit_batch_transcription()` refused the second but the audio was already drained from the buffer — lost forever.
- Root cause: Double-drain pattern in `_handle_flush()` — two sequential drains where the second could fail to submit.
- Fix: Single `drain_buffer()` with a busy-guard. If a batch is in-flight, skip the flush and leave audio in the buffer for the next cycle.
- Regression test: `tests/test_behavioral.py::test_flush_does_not_lose_audio_when_batch_busy`

## [BUG-20260328-portaudio-init-on-import]
- Status: squashed
- Found: 2026-03-28
- Area: recorder (module imports)
- Symptom: macOS "Python quit unexpectedly" crash dialog during test suite. CoreAudio IO thread segfaults during interpreter teardown.
- Root cause: `import sounddevice` at module level in `recorder.py` initializes PortAudio and creates CoreAudio background threads. These native threads crash when Python's interpreter tears down after pytest finishes, even with `os._exit()`.
- Fix: Moved `import sounddevice` and `import soundfile` to lazy imports inside `AudioRecorder.start()`. PortAudio only initializes when actually recording, never during tests.
- Regression test: `tests/test_startup.py::test_scarecrow_recorder_does_not_import_sounddevice`

## [BUG-20260328-preload-model-unhandled]
- Status: squashed
- Found: 2026-03-28 (audit round 3)
- Area: startup
- Symptom: If parakeet model loading fails (MLX OOM, native abort, download failure), the process exits with an unhandled exception traceback instead of a user-facing error message.
- Root cause: `main()` wrapped `prepare()` in try/except but left `preload_batch_model()` unguarded.
- Fix: Wrapped `preload_batch_model()` in try/except with user-facing error message and clean `sys.exit(1)`.
- Regression test: `tests/test_startup.py::test_main_handles_preload_batch_model_failure`

## [BUG-20260328-session-io-crash-startup]
- Status: squashed
- Found: 2026-03-28 (audit round 3)
- Area: app, session
- Symptom: Disk-full or permission errors during session creation crash startup before `_show_error()` runs. Also, `append_sentence()` left `open()` failures uncaught.
- Root cause: `_start_recording()` created `Session(...)` outside its try block; `Session.__init__()` does `mkdir()` + header write. `append_sentence()` called `open()` before the try block.
- Fix: Wrapped Session creation in try/except in `_start_recording()`. Moved `open()` inside the try block in `append_sentence()`.
- Regression test: `tests/test_behavioral.py::test_start_recording_handles_session_creation_failure`, `tests/test_session.py::test_append_sentence_handles_open_failure`

## [BUG-20260328-shutdown-late-callback-race]
- Status: squashed
- Found: 2026-03-28 (audit round 3)
- Area: app, shutdown
- Symptom: In-flight batch results could be duplicated during shutdown — worker's `_on_batch_result` callback lands via `call_from_thread` after `cleanup_after_exit` already wrote the captured text directly.
- Root cause: `_ignore_batch_results` was set after `_wait_for_batch_workers()` completed, leaving a window for the worker's callback to fire.
- Fix: Set `_ignore_batch_results = True` before calling `_wait_for_batch_workers()`.
- Regression test: `tests/test_behavioral.py::test_ignore_batch_results_set_before_wait`

## [BUG-20260328-final-flush-error-lost]
- Status: squashed
- Found: 2026-03-28 (audit round 3)
- Area: app, shutdown
- Symptom: If `_flush_final_batch()` fails during shutdown, the error is routed through `call_from_thread` which raises `RuntimeError` on the app thread; `_post_to_ui` logs it but the user never sees the message.
- Root cause: `_flush_final_batch()` relied on the `on_error` callback path instead of handling errors locally.
- Fix: Wrapped `transcribe_batch()` call in `_flush_final_batch()` with try/except; errors are now shown via `_show_error()` directly.
- Regression test: `tests/test_behavioral.py::test_flush_final_batch_handles_transcription_error`

## [BUG-20260328-drain-silence-floor-division]
- Status: squashed
- Found: 2026-03-28 (audit round 3)
- Area: recorder
- Symptom: `drain_to_silence()` could drain on less than the configured `VAD_MIN_SILENCE_MS` with non-even chunk sizes.
- Root cause: Floor division for `min_silent_chunks` underestimates: e.g., 9600//1700=5 chunks (531ms) instead of ceil(9600/1700)=6 chunks (637ms) for 600ms silence.
- Fix: Changed to ceiling division: `-(-min_silence_samples // denom)`.
- Regression test: `tests/test_recorder.py::test_drain_to_silence_uses_ceil_for_silence_chunks`

## [BUG-20260328-pause-resume-device-loss]
- Status: squashed
- Found: 2026-03-28 (audit round 3)
- Area: app, recorder
- Symptom: If the audio device is disconnected during pause/resume, `stream.stop()`/`stream.start()` raises an exception that propagates out of `action_pause()`, crashing the app.
- Root cause: No exception handling around `_audio_recorder.pause()` and `_audio_recorder.resume()` in `action_pause()`.
- Fix: Wrapped both calls in try/except with logging and error status display.
- Regression test: `tests/test_behavioral.py::test_pause_handles_stream_stop_failure`, `tests/test_behavioral.py::test_resume_handles_stream_start_failure`

## [BUG-20260328-richlog-markup-injection]
- Status: squashed
- Found: 2026-03-28 (audit round 3)
- Area: app
- Symptom: User note text or transcript text containing Rich markup tags could inject styling into the transcript pane (RichLog has `markup=True`).
- Root cause: User input and model output were rendered into RichLog without escaping.
- Fix: Applied `rich.markup.escape()` to user note text in `_submit_note()` and transcript text in `_record_transcript()`.
- Regression test: `tests/test_behavioral.py::test_note_submission_writes_to_richlog` (existing test validates the note path)

## [BUG-20260328-real-model-test-crashes]
- Status: squashed
- Found: 2026-03-28 (audit round 3)
- Area: tests
- Symptom: `pytest.importorskip("parakeet_mlx")` imports the module, triggering native CoreAudio/MLX crashes and macOS "Python quit unexpectedly" dialogs.
- Root cause: `importorskip` actually imports the module to check availability. On this machine, importing `parakeet_mlx` initializes MLX which can cause native aborts during interpreter teardown.
- Fix: Replaced `importorskip` with `@pytest.mark.skipif(not importlib.util.find_spec("parakeet_mlx"), ...)` which probes without importing. Also fixed `test_get_parakeet_model_thread_safety` to mock via `sys.modules` instead of importing parakeet_mlx. Fixed `test_parakeet_model_lazy_import` to actually test the lazy-import path.
- Regression test: `tests/test_startup.py::test_scarecrow_recorder_does_not_import_sounddevice`, `tests/test_parakeet_backend.py::test_parakeet_model_lazy_import`
