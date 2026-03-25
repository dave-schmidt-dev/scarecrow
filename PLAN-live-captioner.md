# Plan: Replace Live Captions with Apple Speech Framework

**Status: Complete (2026-03-25)**

## Goal

Replace the Silero VAD + Whisper base.en live captioning path with Apple's `SFSpeechRecognizer` for true streaming word-by-word captions. Keep Whisper medium.en batch transcription exactly as-is.

## Why this is contained

The app already has clean separation:
- **Live path**: audio → VAD → Whisper base.en → `on_realtime_update` / `on_realtime_stabilized` callbacks → live pane
- **Batch path**: audio → drain buffer → Whisper medium.en → `on_batch_result` callback → transcript pane + file

We replace only the live path. The callbacks (`on_realtime_update`, `on_realtime_stabilized`) stay the same — the app doesn't care where the text comes from.

## Architecture

```
Current:
  AudioRecorder → on_audio → Transcriber._queue → VAD worker → Whisper base.en → callbacks

New:
  AudioRecorder → on_audio → LiveCaptioner (SFSpeechRecognizer) → callbacks
  AudioRecorder → buffer → Transcriber.transcribe_batch (Whisper medium.en, unchanged)
```

New module: `scarecrow/live_captioner.py`
- Owns `SFSpeechRecognizer` + `SFSpeechAudioBufferRecognitionRequest`
- Runs Apple's Cocoa run loop on a dedicated thread
- Accepts audio chunks from the recorder callback
- Emits `on_realtime_update` (partial) and `on_realtime_stabilized` (final) callbacks
- Handles 1-minute session rotation transparently

## Phases

### Phase 1: Proof of concept (standalone script) ✓ Complete

**Work**: Write `scripts/test_apple_speech.py` that:
1. Imports `Speech` and `AVFoundation` via pyobjc
2. Requests speech recognition authorization
3. Creates `SFSpeechRecognizer` with on-device recognition
4. Feeds audio from the microphone via `SFSpeechAudioBufferRecognitionRequest`
5. Prints partial and final results to stdout

**Success criteria**:
- Words appear incrementally as spoken (not in batches)
- Latency < 500ms from speech to first word
- Works fully offline (airplane mode)
- Runs for > 2 minutes with session rotation

**Tests**: Manual only (hardware-dependent). Script should be self-documenting.

**Risk gate**: If PyObjC's block bridging doesn't work with `SFSpeechAudioBufferRecognitionRequest`, we stop here.

### Phase 2: LiveCaptioner module ✓ Complete

**Work**: Create `scarecrow/live_captioner.py`:
- `LiveCaptioner` class with same callback interface as `TranscriberBindings` (partial + stabilized + error)
- Dedicated thread running `CFRunLoopRun()` for Cocoa callbacks
- `accept_audio(chunk)` method matching current `Transcriber.accept_audio` signature
- Session rotation: restart recognition task every ~55 seconds with overlap
- `begin_session()` / `end_session()` / `shutdown()` matching Transcriber's lifecycle API

**Success criteria**:
- `LiveCaptioner` is a drop-in replacement for the live portion of `Transcriber`
- Words appear within ~300ms of being spoken
- Session rotation is seamless (no visible gap in captions)
- `shutdown()` is clean — no leaked threads or Cocoa objects

**Tests**:
- `test_live_captioner.py`: mock-based unit tests for lifecycle, session rotation, error handling
- Integration test with real mic (skipped without hardware, like existing integration tests)

### Phase 3: Wire into the app ✓ Complete

**Work**:
- `ScarecrowApp.__init__` accepts optional `LiveCaptioner`
- `_start_recording` binds `LiveCaptioner` callbacks to the existing `_on_realtime_update` / `_on_realtime_stabilized` handlers
- `AudioRecorder.on_audio` feeds both `LiveCaptioner.accept_audio` and `Transcriber.accept_audio` (for batch buffer)
- `cleanup_after_exit` shuts down `LiveCaptioner`
- `__main__.py` creates `LiveCaptioner` instead of (or alongside) `Transcriber` for live

**Success criteria**:
- Live pane shows streaming word-by-word captions
- Batch pane still shows Whisper medium.en transcript every 30s
- Quit and Ctrl+C clean up both captioner and transcriber
- No regression in batch transcript quality or shutdown behavior

**Tests**:
- Update `test_behavioral.py` shutdown tests to cover `LiveCaptioner` cleanup
- Existing batch tests must pass unchanged

### Phase 4: Remove Whisper live path ✓ Complete

**Work**:
- Remove Silero VAD (`_SileroVAD` class, `silero_vad.onnx` model)
- Remove Whisper base.en live model loading from `ModelManager`
- Remove `Transcriber._run_worker` and the VAD state machine
- Remove `_queue`, `_worker`, `_stop_event` from `Transcriber`
- `Transcriber` becomes batch-only
- Update config: remove `REALTIME_MODEL`, `VAD_*` constants
- Update `__main__.py` startup output

**Success criteria**:
- `onnxruntime` dependency can be removed (if nothing else uses it)
- Startup is faster (no VAD model load, no base.en model load)
- Memory usage drops (~140MB for base.en + ONNX runtime)
- All existing tests updated or removed as appropriate

**Tests**:
- Remove VAD-specific tests (`test_vad_*`)
- Remove Whisper live-specific tests
- Keep all batch, shutdown, and session tests
- Full suite passes

### Phase 5: Polish and document ✓ Complete

**Work**:
- Update README (architecture section, two-model → Apple + Whisper)
- Update BUGS.md (new bug entries if any)
- Update HISTORY.md
- Update TODO.md (remove live pane issues, add any new ones)
- Update `scripts/setup.py` (only batch model selection needed)

**Success criteria**:
- All docs accurate
- `scripts/run_test_suite.sh` passes
- Policy checks pass

## Dependencies

```
pyobjc-framework-Speech
pyobjc-framework-AVFoundation
```

Added to `pyproject.toml`. These are macOS-only — the project already requires macOS.

## What stays the same

- Batch transcription (Whisper medium.en, every 30s)
- Session files (audio.wav + transcript.txt)
- Shutdown/cleanup paths (same entrypoints, same ordering)
- AudioRecorder (unchanged — still captures and buffers)
- TUI layout and keybindings
- All batch and shutdown tests

## Risks

| Risk | Mitigation |
|------|-----------|
| PyObjC block bridging fails | Phase 1 is a standalone test — find out before touching the app |
| 1-min session limit causes gaps | Overlap rotation: start new session 5s before old one ends |
| On-device accuracy worse than base.en | Test side-by-side in Phase 1; can abort if unacceptable |
| NSRunLoop conflicts with Textual event loop | LiveCaptioner runs its own thread with its own run loop |
| SFSpeechRecognizer not available (old macOS) | Graceful fallback to current Whisper live path |

## Open questions (answered in Phase 1)

1. Does `pyobjc-framework-Speech` actually work for streaming recognition?
2. What's the real latency on this machine?
3. Is on-device accuracy acceptable for live captions?
4. Does the 1-minute rotation work seamlessly?
