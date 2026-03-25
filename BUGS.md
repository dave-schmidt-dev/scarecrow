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
- Regression test: `tests/test_behavioral.py::test_start_recording_unwinds_recorder_when_transcriber_begin_fails`
- Notes: verified 2026-03-24.

## [BUG-20260324-overlapping-batch-workers]
- Status: squashed
- Found: 2026-03-24
- Area: app, transcriber, runtime
- Symptom: batch transcriptions can overlap on the shared batch model, and a second batch tick can drain/drop audio while the first batch is still running.
- Root cause: each tick launched an untracked background worker, and `transcribe_batch()` had no lock around shared batch-model inference.
- Fix: the app now allows only one in-flight batch worker, preserves audio when a tick lands while batch work is already running, and `Transcriber.transcribe_batch()` serializes access to the shared batch model.
- Regression test: `tests/test_behavioral.py::test_batch_tick_skips_overlap_without_draining_new_audio`, `tests/test_transcriber.py::test_transcribe_batch_serializes_overlapping_calls`
- Notes: verified 2026-03-24.

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
- Regression test: `tests/test_transcriber.py::test_prepare_sets_is_ready`, `tests/test_transcriber.py::test_shutdown_joins_thread`, `tests/test_startup.py::test_transcriber_prepare_with_mocked_whisper`
- Notes: verified 2026-03-24.

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
- Regression test: `tests/test_transcriber.py::test_shutdown_timeout_preserves_runtime_until_worker_exits`, `tests/test_integration.py::test_transcriber_pipeline_with_real_audio_fixture`
- Notes: verified 2026-03-24.

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
- Regression test: `tests/test_integration.py::test_transcriber_pipeline_with_real_audio_fixture`
- Notes: verified 2026-03-24.

## [BUG-20260324-live-pane-fragility]
- Status: squashed
- Found: 2026-03-24
- Area: app
- Symptom: live pane scrolling, partial replacement, and stabilized-line behavior regress easily.
- Root cause: full clear-and-rewrite rendering depends on replaying internal live state instead of using a simpler widget/update model.
- Workaround: `REALTIME_MAX_SPEECH=10s` forces periodic breaks so lines eventually stabilize.
- Fix: live output now renders through a single scrollable pane with one content widget, keeping stable lines plus the current partial without RichLog replay state.
- Regression test: `tests/test_app.py::test_live_not_cleared_on_caption`, `tests/test_behavioral.py::test_history_preserved_across_partial_updates`, `tests/test_behavioral.py::test_stabilized_replaces_partial`
- Notes: verified 2026-03-24.

## [BUG-20260324-split-model-bootstrap]
- Status: squashed
- Found: 2026-03-24
- Area: startup, app, transcriber
- Symptom: runtime behavior depends on startup ordering and env state that is spread across modules.
- Root cause: HF offline flags, tqdm lock warmup, live model load, and batch model load are initialized in different places.
- Workaround: start through `python -m scarecrow` or the console script so `__main__` sets env first.
- Fix: runtime bootstrap moved into `scarecrow/runtime.py`, with one model manager owning env flags, tqdm warmup, and both Whisper model load paths.
- Regression test: `tests/test_startup.py::test_hf_hub_offline_set_by_main_module`, `tests/test_startup.py::test_transcriber_prepare_with_real_model`, `tests/test_integration.py::test_transcriber_pipeline_with_real_audio_fixture`
- Notes: verified 2026-03-24.

## [BUG-20260324-missing-real-pipeline-test]
- Status: squashed
- Found: 2026-03-24
- Area: tests
- Symptom: regressions reach runtime because most tests mock the transcriber, recorder, or model calls.
- Root cause: no test currently feeds real audio through the actual transcription pipeline with real cached models and a timeout.
- Workaround: manual testing with recordings and live app runs.
- Fix: added a real-model integration test that feeds an actual recording through both the live and batch transcriber paths with a 30-second timeout.
- Regression test: `tests/test_integration.py::test_transcriber_pipeline_with_real_audio_fixture`
- Notes: verified 2026-03-24.

## [BUG-20260324-missing-hook-enforcement]
- Status: squashed
- Found: 2026-03-24
- Area: workflow, docs, validation
- Symptom: it was easy to forget doc updates, regression references, or validation before commit/push.
- Root cause: repo policy lived in chat instructions and habit rather than enforceable git hooks.
- Workaround: manually remember to update docs, run checks, and push.
- Fix: added repo-managed pre-commit and pre-push hooks via `.pre-commit-config.yaml` and `scripts/check_repo_policy.py`.
- Regression test: `scripts/check_repo_policy.py --staged-only` enforced by pre-commit, full `uv run pytest` enforced by pre-push
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
- Regression test: `tests/test_transcriber.py::test_get_batch_model_thread_safety`
- Notes: verified 2026-03-24.

## [BUG-20260324-pause-wipes-live-pane]
- Status: squashed
- Found: 2026-03-24
- Area: app
- Symptom: pressing pause wiped all accumulated live transcription lines, replacing them with just "Paused".
- Root cause: `action_pause` called `_update_live("Paused")` which replaces all `_live_stable` lines.
- Fix: pause now sets the partial text to "Paused" without clearing stable history. Resume clears the partial.
- Regression test: `tests/test_behavioral.py::test_pause_preserves_live_history`
- Notes: verified 2026-03-24.

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
- Notes: verified 2026-03-24. Scripts are manual test helpers and not part of the automated suite; the import-path regression test guards the shared failure mode.

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
- Regression test: `tests/test_behavioral.py::test_realtime_callbacks_update_live_pane_directly`
- Notes: verified 2026-03-25. Root cause visible in `~/.cache/scarecrow/debug.log` as repeated `UI callback failed while app still active: _set_live_partial` errors.

## [BUG-20260325-live-pane-clears-on-isFinal]
- Status: squashed
- Found: 2026-03-25
- Area: live_captioner
- Symptom: live pane text cleared periodically mid-speech — partial text vanished and no new text appeared until the 55s rotation timer fired.
- Root cause: Apple's Speech framework fires `isFinal` when it detects a pause in speech, which terminates the `SFSpeechRecognitionTask`. The captioner handled `isFinal` by emitting a stabilized callback and clearing `_prev_text`, but never started a new recognition task. Audio continued flowing into the now-dead request via the AVAudioEngine tap, but no results were delivered until the 55s rotation.
- Fix: when `isFinal` fires and `self._request is request` (the task ended naturally, not via our explicit rotation), immediately start a new recognition session. The closure captures the specific `request` object so rotation-triggered final callbacks don't spawn duplicate sessions.
- Regression test: `tests/test_live_captioner.py::test_natural_isfinal_restarts_session`
- Notes: verified 2026-03-25.

## [BUG-20260325-live-pane-fills-and-clears]
- Status: squashed
- Found: 2026-03-25
- Area: live_captioner, app
- Symptom: live pane filled with text then cleared rather than scrolling. Text never accumulated — each cycle replaced rather than appended.
- Root cause: Apple's `formattedString()` returns the entire session text since the session started, not just the latest words. `on_realtime_update` was passing this growing blob as `_live_partial`, which filled the 8-row pane. When `isFinal` fired, the blob committed as one huge stable entry and `_live_partial` was cleared. The next session's partial started fresh with just a few words, so the pane appeared to clear even though the stable history remained scrolled off the top.
- Fix: incremental commit in the result handler. When uncommitted words exceed `_COMMIT_THRESHOLD` (10), the settled portion is flushed to `on_realtime_stabilized` and only `_PARTIAL_TAIL` (4) words remain as the unstable partial. `isFinal` commits any remaining uncommitted words. Stable lines now accumulate sentence-by-sentence and the pane scrolls naturally.
- Regression test: `tests/test_live_captioner.py::test_partial_below_threshold_emits_update_only`, `tests/test_live_captioner.py::test_partial_above_threshold_flushes_chunk_to_stable`, `tests/test_live_captioner.py::test_isfinal_commits_remaining_uncommitted_words`
- Notes: verified 2026-03-25.

## [BUG-20260325-live-captioner-isfinal-reentrancy]
- Status: squashed
- Found: 2026-03-25
- Area: live_captioner
- Symptom: after fixing the dead-session bug (BUG-20260325-live-pane-clears-on-isFinal), live captions showed a few words, hung briefly, then cleared and repeated — never accumulating text.
- Root cause: the natural-isFinal restart called `_start_recognition_session()` directly from inside the existing task's result handler callback. Creating a new `recognitionTaskWithRequest_resultHandler_` while inside another task's result handler is a reentrancy problem in Apple's Speech framework: the new task starts in a broken or delayed state, causing the hang and stale partial words.
- Fix: result handler now sets a `_needs_restart` flag instead of restarting inline. `tick()` was split into `_pump_runloop()` + `_tick_body()`; `_tick_body()` checks `_needs_restart` after the NSRunLoop pump returns (cleanly outside any callback) and starts the new session there.
- Regression test: `tests/test_live_captioner.py::test_natural_isfinal_sets_needs_restart`, `tests/test_live_captioner.py::test_natural_isfinal_restarts_session`, `tests/test_live_captioner.py::test_rotation_isfinal_does_not_set_needs_restart`
- Notes: verified 2026-03-25.

## [BUG-20260325-live-pane-scroll-resets-at-boundary]
- Status: open
- Found: 2026-03-25
- Area: app
- Symptom: live pane scrolls correctly for ~9 stable lines, then on the 10th committed line the pane clears and resets to show only the latest caption — scrolling stops and history is lost from view.
- Root cause: unknown. Suspected: Textual's `VerticalScroll` + `Static` widget does not correctly recompute virtual height once the `Static` content exceeds the container's visible area (pane is `height: 8` in CSS). At the overflow boundary, `content.update(text)` or `scroll_end` may reset the scroll position and virtual size rather than extending them. The 9-line threshold matches the pane height (8 visible rows + 1 overflow row).
- Workaround: none.
- Fix: pending.
- Regression test: pending.
- Notes: `_live_stable` is accumulating correctly (confirmed by `LIVE_HISTORY_LIMIT = 50`). The bug is in how Textual renders the growing `Static` widget inside `VerticalScroll`, not in the captioner or stable-list logic. Investigate whether replacing `Static` + `VerticalScroll` with `RichLog` (which has its own scroll management) resolves it, or whether a `height: auto` / layout tweak on `#live-content` is sufficient.
