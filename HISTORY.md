# History

## 2026-03-28 (improvements: JSONL, FLAC, retry, pruning, cleanup)

- **RichLog pruning:** UI transcript pane now caps at 500 lines. Oldest lines are pruned automatically; all content is on disk in the JSONL transcript.
- **Removed dead `requests` dependency** from pyproject.toml (never imported).

## 2026-03-28 (JSONL transcript format)

- **Switched transcript format from plain text to JSON Lines:** Each event is a JSON object on its own line in `transcript.jsonl`. Replaces `Session.append_sentence(str)` with `Session.append_event(dict)`. Events: `session_start`, `session_end`, `transcript`, `divider`, `pause`, `note`, `warning`.
- **Updated all callers in `app.py`:** `_record_transcript`, `_warn_transcript`, `_submit_note`, and `_write_pause_marker` now emit typed JSON events instead of formatted plain-text lines. UI rendering is unchanged.
- **Updated all tests:** `test_session.py` and `test_behavioral.py` rewritten to parse JSONL and assert on event types and fields.

## 2026-03-28 (batch transcription retry with graceful degradation)

- **Added retry logic to `transcribe_batch()`:** On failure, retries up to 3 times total (1 initial + 2 retries) with a 0.5s delay between attempts before giving up.
- **Added `consecutive_failures` property:** Tracks consecutive batch transcription failures; resets to 0 on the next successful transcription. Allows callers to detect sustained model degradation.
- **Updated error message:** Exhausted-retries error now reads "Batch transcription failed after retries. Audio is still recording." for clearer user feedback.
- **Added 3 tests:** `test_transcribe_batch_retries_on_failure`, `test_transcribe_batch_exhausts_retries`, `test_transcribe_batch_resets_failures_on_success`.

## 2026-03-28 (post-session FLAC compression)

- **Added FLAC compression:** After recording stops, `Session.compress_audio()` reads `audio.wav` via soundfile, writes a lossless `audio.flac`, and deletes the WAV (~2:1 size reduction). Called in `cleanup_after_exit()` just before `finalize()`.
- **Added `Session.final_audio_path` property:** Returns `audio.flac` if it exists, falls back to `audio.wav`.
- **Added 4 tests:** `test_compress_audio_creates_flac`, `test_compress_audio_returns_none_when_no_wav`, `test_final_audio_path_prefers_flac`, `test_final_audio_path_falls_back_to_wav`.

## 2026-03-28 (audit round 3: error handling, shutdown races, test gaps, docs)

- **Fixed unhandled preload_batch_model() crash:** `main()` now wraps `preload_batch_model()` in try/except for clean error output and exit on model load failure (MLX OOM, native abort, download failure).
- **Fixed Session I/O crash on startup:** `_start_recording()` now wraps `Session()` creation in try/except so disk-full or permission errors surface via `_show_error()` instead of crashing. `Session.append_sentence()` now catches `OSError` from `open()` as well as `write()`/`flush()`.
- **Fixed shutdown late-callback race:** `_ignore_batch_results` is now set BEFORE `_wait_for_batch_workers()`, closing the window where a worker's `_on_batch_result` callback could duplicate text already captured from the future.
- **Fixed final-flush error loss:** `_flush_final_batch()` now catches exceptions from `transcribe_batch()` locally instead of relying on the callback path (which routed through `call_from_thread` and was silently lost on the app thread).
- **Fixed drain_to_silence() floor division:** Changed from floor to ceiling division for `min_silent_chunks`, preventing premature drains with non-even chunk sizes (e.g., 1700-sample chunks at 600ms silence threshold).
- **Fixed pause/resume device-loss crash:** `action_pause()` now wraps `recorder.pause()` and `recorder.resume()` in try/except so device disconnection doesn't crash the app.
- **Fixed Rich markup injection:** User note text and transcriber output are now escaped via `rich.markup.escape()` before rendering in the RichLog, preventing markup injection from transcript text or notes.
- **Fixed real-model test crashes:** Replaced `pytest.importorskip("parakeet_mlx")` with `importlib.util.find_spec()` checks in test_startup.py, test_integration.py, and test_transcriber.py — probes without importing, avoiding native CoreAudio/MLX crashes. Fixed `test_parakeet_model_lazy_import` to actually test the lazy-import path instead of mocking the method under test.
- **Added missing failure path tests:** preflight check (no devices, device query failure), recorder warning surfacing, pause/resume device-loss, session creation failure, transcript open failure, shutdown race flag timing, final-flush error handling.
- **Fixed BUGS.md stale entries:** Updated 16 squashed bug entries pointing to deleted tests, renamed tests, or non-test regression references. Tightened policy checker to require `tests/` or `::` in regression test values for squashed bugs, skip won't-fix bugs.
- **Fixed stale docs:** README pause marker frequency, batch executor shutdown description, removed non-existent startup cache output. Updated `__init__.py` docstring, `test_audio_capture.py` docstring, startup hint text.
- **Added pytest warning to README:** Explicit "do not run pytest directly" note in Development section explaining the Textual segfault and directing to `run_test_suite.sh`.

## 2026-03-28 (audit fixes, lazy imports, /flush safety)

- **Fixed /flush audio loss bug:** `/flush` was doing a double-drain (VAD then full drain) which could lose audio if a batch was already in-flight. Now does a single `drain_buffer()` with a busy check — if a batch is running, audio stays in the buffer.
- **Lazy sounddevice/soundfile imports:** Moved `import sounddevice` and `import soundfile` from module-level to inside `AudioRecorder.start()`. Prevents PortAudio from initializing during tests, eliminating CoreAudio thread crashes entirely.
- **Deleted stale scripts:** Removed `scripts/test_transcription.py` and `scripts/test_dual_stream.py` (dead RealtimeSTT-era code).
- **Removed dead CSS:** Deleted `#context-display` block from `app.tcss`.
- **Synced benchmark VAD params:** Updated `bench_librispeech.py` to match current config (600ms/30s).
- **Added /flush tests:** 3 behavioral tests covering drain, busy-guard, and idle-noop.
- **Added VAD/recorder tests:** drain_to_silence, buffer_seconds, empty-buffer edge case.

## 2026-03-28 (VAD tuning, /flush command, cleanup)

- **VAD tuning:** `VAD_MIN_SILENCE_MS` 300→600ms (drain on real sentence-ending pauses, not mid-sentence hesitations), `VAD_MAX_BUFFER_SECONDS` 8→30s (avoid forced mid-speech splits). Reduces chunk-boundary transcription errors significantly.
- **Removed audio overlap:** Overlap was a whisper-era concept that caused duplicate text with parakeet (see commit 70f871c). Removed `overlap_ms` parameter, `_overlap_tail` state, and overlap logic from `AudioRecorder` entirely.
- **Added `/flush` (`/f`) command:** Force-drains the audio buffer and submits for transcription immediately, regardless of silence detection. Useful when you want text to appear without waiting for a pause.
- **Removed `/note` (`/n`) command:** Redundant — typing anything without a prefix already creates a `[NOTE]`.
- **Fixed buffer time jitter:** The 1-second tick timer was decrementing `_batch_countdown` independently while the VAD poll (every 150ms) was setting it to the actual buffer size — they fought each other causing non-linear display. Removed the tick decrement; only the VAD poll updates the buffer display now.
- **Startup display:** Replaced "Batch interval: 5s" with "Chunking: VAD (drains at speech pauses)" to accurately reflect VAD behavior.
- **Dead code cleanup:** Removed unused `on_audio` callback parameter from `AudioRecorder`, removed `has_active_worker` (always False) from `Transcriber`, fixed stale whisper references in comments/docstrings.
- **Crash mitigation:** Added `_sounddevice.terminate()` before `os._exit()` in test runner to shut down CoreAudio threads cleanly, preventing macOS crash reporter popups.

## 2026-03-28 (whisper removal migration)

- Removed `faster-whisper` backend entirely; `parakeet-mlx` is now the only transcription engine
- Promoted `parakeet-mlx` from optional to required dependency in pyproject.toml
- Removed context injection system (`/context`, `/clear` commands, initial_prompt, rolling tail)
- Simplified runtime.py: removed WhisperModel, model_cache_path, warm_tqdm_lock
- Simplified transcriber.py: removed whisper dispatch, _batch_lock, initial_prompt parameter
- Simplified app.py: removed backend branching, context state, whisper batch timer
- Removed whisper-specific config constants (FINAL_MODEL, LANGUAGE, BEAM_SIZE, BACKEND, etc.)
- Updated test suite: deleted ~23 whisper/context tests, rewrote ~24 tests for parakeet-only
- Simplified setup script and benchmark to parakeet-only paths

## 2026-03-28 (migration plan: whisper removal)

- Added `PLAN-whisper-removal.md` with phased migration spec for removing faster-whisper backend entirely. 10 phases with verification gates, risk assessment, and commit grouping.

## 2026-03-28 (parakeet: UI polish, auto-start, notes shortcuts)

- **Auto-start recording:** Parakeet backend starts recording immediately on launch — no Enter prompt needed (whisper still shows context prompt).
- **Notes shortcuts:** Added `/n` and `/note` as explicit shortcuts for plain notes. Updated notes label to show `/t task  /n note  /help`.
- **Removed redundant status:** Dropped "Listening..." from info bar (REC indicator is sufficient). Buffer counter now updates continuously.
- **Transcript label:** Shows "VAD" instead of "every 5s" for parakeet backend.
- **Context commands:** Hidden from help and UI labels for parakeet (not supported). Whisper backend unchanged.

## 2026-03-28 (parakeet: VAD chunking, bug fixes, benchmarking)

- **Fixed audio duplication bug:** `audio[-0:]` returns the full array in numpy; when `overlap_ms=0` (parakeet), every batch contained all previous audio. Fixed by skipping overlap logic when `overlap_samples == 0`.
- **Paragraph joining:** Consecutive batch results are now space-joined into flowing paragraphs in the RichLog transcript pane, instead of one line per batch. Paragraphs reset on dividers, notes, warnings, and pause markers. Uses `RichLog.lines` splice + cache clear to update in-place.
- **VAD-based chunking:** Parakeet backend now uses silence detection instead of fixed 5-second timer. Audio drains at natural speech pauses (300ms+ silence), with 8-second hard max for continuous speech. Poll interval: 150ms. Whisper backend unchanged (fixed 15s timer).
- **Silence drain includes trailing gap:** VAD drain now includes the silent chunks after speech, preventing word clipping at trailing edges of utterances.
- **200ms audio overlap for parakeet:** Added small overlap between VAD chunks to catch words at silence boundaries (was 0ms, whisper uses 500ms).
- **Divider interval increased to 60s:** Transcript timestamp dividers now fire every 60s (was 30s) for less interruption during continuous transcription.
- **Batch timing logs:** Parakeet transcription now logs audio duration, wall time, and RTF to debug log for duty cycle monitoring.
- **LibriSpeech benchmarking:** Added `benchmarks/bench_librispeech.py` with LibriSpeech test-clean dataset. Supports fixed chunking (`--chunk N`) and VAD chunking (`--vad`). Tracks speed (RTF), accuracy (WER with punctuation normalization), CPU, RSS, and MLX GPU memory. Added `benchmarks/gpu_monitor.sh` for Apple Silicon GPU power monitoring.
- **Benchmark results (3 min, 15s fixed chunks, normalized WER):** Parakeet 18.7% WER / 0.006x RTF / 50% CPU / 1.5 GB RSS / 2.2 GB GPU. Whisper 3.6% WER / 0.30x RTF / 400% CPU / 3.5 GB RSS. Parakeet ~47x faster. Individual utterance accuracy is perfect (0% WER) — chunked WER is from boundary artifacts.
- **GPU power draw:** ~45-50mW idle between chunks, 400-900mW during transcription bursts. Under 1W peak — negligible battery impact.

## 2026-03-27 (docs update: open bugs and TODO refresh)

- Added BUG-20260327-parakeet-batch-newlines to BUGS.md: RichLog.write() creates newlines per batch, causing noisy transcript pane at 5-second intervals.
- Refreshed TODO.md with parakeet branch status, GPU findings, and known limitations.

## 2026-03-27 (test independence: whisper tests patch BACKEND explicitly)

- Added `@patch("scarecrow.config.BACKEND", "whisper")` to all whisper-specific tests so they pass regardless of the config.py setting. Tests are now backend-independent.

## 2026-03-27 (fix repeated text: disable audio overlap for parakeet)

- Disabled 500ms audio overlap for parakeet backend (overlap_ms=0). The overlap was designed for Whisper's initial_prompt context continuity; parakeet doesn't use it, and the overlap caused repeated phrases at every 5-second batch boundary.
- Reverted paragraph buffer approach (RichLog doesn't support in-place line updates). Each batch gets its own line — acceptable with 30-second divider throttling.
- Made overlap_ms configurable on AudioRecorder (keyword argument, default 500ms for whisper).

## 2026-03-27 (paragraph joining: space-joined batch results in transcript pane)

- Consecutive batch results between dividers are now space-joined into a single paragraph in the RichLog UI, instead of each batch getting its own line. Particularly important for the 5-second parakeet batch interval.
- Transcript file still writes one line per batch (unchanged).
- Paragraph resets on dividers, pause markers, and resume.

## 2026-03-27 (parakeet integration fix: numpy array support)

- Fixed parakeet-mlx integration: `transcribe()` only accepts file paths, not numpy arrays. Now uses `get_logmel()` + `model.generate()` to process numpy arrays directly from the recorder buffer.
- Updated parakeet backend tests to mock at the correct level.

## 2026-03-27 (bugfixes: model download + recordings path)

- Fixed recordings directory using a relative path (`Path("recordings")`) — output landed in different directories depending on the working directory at launch. Now absolute: `~/recordings/`.
- Fixed `model_cache_path` to handle full HF repo IDs (e.g. `deepdml/faster-whisper-large-v3-turbo-ct2`) in addition to short model names.
- Fixed first-run model download failing due to `HF_HUB_OFFLINE=1` — `_create_model` now temporarily lifts offline mode when the model isn't cached yet.

## 2026-03-27 (feature branch: parakeet-mlx backend)

- Added parakeet-mlx as alternative transcription backend alongside faster-whisper.
- Backend selection via `config.BACKEND` (`"whisper"` or `"parakeet"`) and `scripts/setup.py`.
- Parakeet runs on Apple Silicon GPU via MLX: ~0.5s inference, ~2x better accuracy (1.9% vs 3.0% WER).
- Dynamic batch interval: 5s for parakeet, 15s for whisper.
- Divider throttling: transcript dividers appear every 30s regardless of batch interval, reset on pause/resume.
- `parakeet-mlx` is an optional dependency (`pip install -e ".[parakeet]"`).
- Startup output shows active backend, model, and batch interval.
- Setup script (`scripts/setup.py`) now includes backend selection step.
- Added 8 new tests in `tests/test_parakeet_backend.py`.
- Note: Parakeet does not support `initial_prompt` context injection — context entries are still collected but not passed to the model.

## 2026-03-27 (model upgrade: medium.en → large-v3-turbo)

- Switched default batch model from `medium.en` to `large-v3-turbo` (better accuracy, fewer decoder layers = faster inference despite larger encoder).
- Added `large-v3-turbo` to setup.py model selection list with recommended tag.
- Updated all docs (README, TODO, setup.py) to reflect the new default model.

## 2026-03-27 (docs refresh)

- Added `/help` command to README feature list.
- Fixed stale comment in `_submit_note` referencing removed `/action`, `/followup` prefixes.
- Refreshed TODO.md: removed resolved pause/resume items, added screen reader accessibility tracking note.

## 2026-03-27 (batch monitoring + narrow terminal support)

- Batch executor health monitoring: `_reap_batch_futures()` now checks completed futures for exceptions and surfaces failures as `[WARNING]` in the transcript instead of silently discarding them.
- Narrow terminal support: InfoBar drops word count below 60 columns and batch countdown below 50 columns to prevent wrapping.

## 2026-03-27 (resilience hardening)

- Disk-full handling: audio writes and transcript writes now catch OSError, surface `[WARNING]` in transcript, and continue without crashing.
- Sounddevice status monitoring: callback now checks the `status` parameter for input overflow/underflow/device errors, surfaces warnings in transcript with `[WARNING]` tag.
- Wall-clock elapsed timer: replaced tick-counting (`_elapsed += 1`) with `time.monotonic()` delta so sleep/wake doesn't cause timer drift.
- Warning infrastructure: added `_warn_transcript()` helper and `_check_recorder_warnings()` polling in the 1-second tick, using `[WARNING]` tag format recognizable by future summarizers.
- Min-height CSS: transcript pane now has `min-height: 3` to prevent collapse on small terminals.

## 2026-03-27 (UX and accessibility fixes)

- Added `/help` command (also `/h` and `?`) showing inline command reference and keybindings.
- Audio meter now shows text labels ("quiet" / "LOUD") alongside color for colorblind accessibility.
- Context prompt includes examples: "e.g. Alice, React, Q4 planning".
- Fixed stale `scripts/setup.py` referencing removed Apple Speech live captions and 30s batch interval.

## 2026-03-27 (test parallelization)

- Parallelized test suite runner: independent test files run concurrently (Phase 1), behavioral nodes run in parallel batches of 8 (Phase 2). Wall time reduced from ~60s to ~20s on M5 Max.

## 2026-03-27 (cleanup: dead model, plan docs, dependency pinning, preload, docs)

- Removed dead Silero VAD model (`silero_vad.onnx`) and `onnxruntime` dependency.
- Removed completed PLAN docs (`PLAN-live-captioner.md`, `PLAN-context-injection.md`, `PLAN-notes-pane-rehaul.md`).
- Pinned `faster-whisper>=0.9,<2.0` in `pyproject.toml` for version stability.
- Added model preloading at startup: batch model loads during the prepare phase (before TUI launches), not on first batch.
- Updated `.gitignore` to explicitly cover `.pytest_cache/` and `.ruff_cache/`.
- Fixed hardcoded absolute path in README bug tracking link (`BUGS.md`).
- Added Troubleshooting section to README (offline mode, mic permissions, venv repair, debug logs).
- Simplified README setup instructions with a decision tree for launch method selection.
- Updated README architecture file tree to match current repo state.

## 2026-03-27 (branding: scarecrow icon and startup banner)

- Added SVG scarecrow icon (`assets/scarecrow-icon.svg`) — Wizard of Oz inspired, holding a microphone.
- Added ASCII art startup banner in the transcript pane on launch (scrolls away when recording begins).
- Icon displayed in README header.

## 2026-03-27 (v1.0 release)

- Removed development debug logging (meter sync, batch prompt).
- Tagged v1.0 — MVP complete: batch transcription, notes pane, context injection, audio meter.

## 2026-03-27 (polish: /task consolidation, notation counts, audio meter, session timestamps)

- Merged `/action` + `/followup` into `/task` (shorthand `/t`). Simpler taxonomy: `/context` for background, `/task` for action items, plain text for notes.
- Context display line now shows entry counts: "Context: 3 · Tasks: 2 · Notes: 1" instead of raw context text.
- Added audio level meter (▁▂▃▄▅▆▇█) in InfoBar during recording — confirms mic is active at a glance.
- Meter uses log scale: -46dB to -10dB mapped to bar characters. Color coded: green = quiet, yellow = normal, red = loud.
- Peak hold with decay: peak value decays by 0.15 per read so the indicator doesn't freeze at transient peaks.
- Session timestamps: transcript files now open with `Session Start: YYYY-MM-DD HH:MM:SS` and close with `Session End: YYYY-MM-DD HH:MM:SS`. Session start timestamp is also shown in the RichLog UI pane at launch.
- Added debug logging of the batch prompt sent to Whisper for context verification.

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
