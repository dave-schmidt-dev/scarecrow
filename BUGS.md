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
