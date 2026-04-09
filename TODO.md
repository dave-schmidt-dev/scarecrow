# Roadmap

## System audio (Phase 1 + 2 + 3 done)
- [x] Capture system audio via BlackHole to separate WAV (on by default, `--no-sys-audio` to disable)
- [x] Dual InfoBar level meters (mic + sys)
- [x] Streaming FLAC compression on shutdown
- [x] Auto-switch to Scarecrow Output on startup, restore on exit
- [x] Transcribe both channels via Parakeet (shared executor, mic priority)
- [x] Left-aligned mic, right-aligned italic sys transcripts in TUI
- [x] JSONL `"source": "mic"|"sys"` field on transcript events
- [x] Per-source mute (Ctrl+M mic, Ctrl+Shift+S sys)
- [x] Tune VAD thresholds via multi-session WER benchmark
  - Fixed FLAC pre-gain bug, upgraded primary metric to WER (token-level alignment)
  - Swept across 3 diverse recordings (lecture, group call, phone call)
  - Sys: threshold 0.003→0.004, silence 750→1500ms, buffer 5→7s (WER -18–23%)
  - Mic: threshold 0.01→0.003, silence 750→1250ms (WER -8–24%)
  - Results: `benchmarks/vad_sweep_2026-04-05.md`
- [x] Wire up `EchoFilter.record_mic()` / `is_sys_echo()` — bidirectional suppression wired in app.py (2026-04-02)
- [x] **Phase 3: Replace BlackHole with CoreAudio Process Tap API (macOS 14.2+)** (2026-04-07)
  - `audio_tap.py` — PyObjC for CATapDescription, ctypes for aggregate device
  - `_coreaudio.py` — shared CoreAudio/CoreFoundation helpers
  - Private aggregate device with tap auto-start
  - App owns tap lifecycle (in `_start_recording()`/`_cleanup_stop_recorder()`)
  - Deleted `audio_routing.py` and BlackHole device switching
  - `pyobjc-framework-CoreAudio` dependency added
  - Requires System Audio Recording permission (Privacy & Security)
  - Eliminates: BlackHole install, Audio MIDI Setup, device routing complexity
  - [x] Fixed 3x sample rate mismatch: tap-only aggregate (no sub-devices), explicit rate/buffer config
  - [x] Fixed silence hallucination: enabled SYS_VAD_MIN_SPEECH_RATIO (0.05)
  - [x] Full sys VAD re-sweep (2026-04-09): threshold 0.04→0.01, silence 300→1250ms
    - Swept across 2-hour lecture + 40-min multi-speaker huddle (both Process Tap)
    - Fixed replay_test.py bug: was using 30s mic max_buffer instead of 10s sys max_buffer
    - Lecture WER: 0.075→0.036 (52% reduction), Huddle WER: 0.174→0.104 (40% reduction)
    - Results: `benchmarks/vad_validation_2026-04-09.md`

## Launch-time audio source flags
- [x] `--mic-only` / `--sys-only` CLI flags to start with one source muted
- [x] Still configurable at runtime via existing mute toggles

## Level meter interaction
- [x] Left-click on mic/sys level meter to toggle mute
- [x] Ctrl+V opens VAD / mute menu: mute toggle + Low/Normal/High sensitivity (input gain) for mic and sys

## Auto-segmentation for long sessions
- [x] Automatically create segment boundaries at ~60 minute marks
- [x] Each segment gets its own summary while maintaining full transcript continuity
- [x] Supports 2-3 hour lectures/classes without manual splitting
- [x] BUG: transcription drops after segment rotation — _rotate_segment() discarded drain results instead of submitting for transcription
- [x] BUG: summary file numbering skips segment 3 — empty segments now get a placeholder summary

## TurboQuant KV-cache compression (evaluated — not worth enabling)
- [x] Evaluate TurboQuant (mlx-vlm `kv_bits` parameter) for summarizer inference
  - Fixed bug: kv_bits was passed to `load()` (ignored) instead of `generate()` (where mlx-vlm uses it)
  - Fixed: must use `kv_quant_scheme="turboquant"` — default "uniform" crashes on Gemma 4's RotatingKVCache
  - Benchmarked kv_bits=4 and kv_bits=8 across 3 transcripts (2K/8K/19K words)
  - Result: negligible memory savings (<0.1 GB on 18 GB footprint), kv_bits=8 is 14% slower
  - KV cache is tiny relative to model weights at these context lengths — not worth enabling
  - Leave `SUMMARIZER_MLX_KV_BITS = None`; results in `benchmarks/kv_eval_results/`

## Summarizer model swap
- [x] Replace Nemotron-3-Nano with Gemma 3 27B IT (Google) for summarization
- [x] Nemotron had 57% failure rate: CoT leaking, repetition loops, unreliable structured output
- [x] Gemma 3 27B: best structured output compliance, 128K context, strong summarization benchmarks
- [x] Length-scaled prompts: short recordings get concise summaries, long sessions get comprehensive coverage
- [x] Replace Gemma 3 27B (GGUF) with Gemma 4 26B MoE (MLX) — 8x faster, 2.4x less RAM, comparable quality
- [x] Remove SUMMARIZER_MIN_CTX (128K floor) — dynamic sizing handles this correctly

## Diarization (evaluated — integration planned)
- [x] Benchmark pyannote-audio 4.0 with speaker-diarization-3.1 model
  - 95.2% accuracy on compressed mono Signal call audio (2 speakers, human-annotated ground truth)
  - MPS acceleration: 0.04x RTF (38-min file in 91s), identical accuracy to CPU
  - community-1 model tested: no improvement over 3.1
  - Results: `benchmarks/diarization_eval/`
- [x] Integration: `/speakers mic:Dave sys:Mike,Justin` command, post-session diarization, speaker-attributed summaries
  - Plan: `~/.claude/plans/majestic-noodling-toast.md` (reviewed by contrarian x2 + Codex)
  - New `scarecrow/diarizer.py` module, summarizer prompt changes, MPS default with CPU fallback

## Auto-summarization (done)
- Local LLM summarization on shutdown via Gemma 4 26B MoE (MLX default, GGUF fallback)
- Prompt extracts: executive summary, key points, action items (explicit [TASK] + implicit follow-ups)
- Handles [NOTE], [TASK], [CONTEXT] tags
- Auto-syncs summaries to Obsidian vault
- Manual re-run via scripts/resummarize.py

## TUI transcript batching lag
- [x] UI appeared to dump 10-15 individual transcript lines at once after 20-30s delay
  - Root cause: VAD fragmentation (300ms silence / 2.0s buffer) produced ~2s segments with 2-5 words each — many tiny lines scrolling fast gave the appearance of batching
  - Transcript JSONL analysis confirmed median latency was only 3.0s (P95: 6.0s) — no actual UI batching
  - Fixed by VAD re-sweep: 1250ms silence produces 5-8s segments with complete thoughts

## Live captions (speculative)
- Short-buffer preview via Parakeet, replaced by VAD-final text
- Parakeet at 0.010x RTF makes less than 1s preview latency feasible
- Speculative text visually distinct from committed transcript
- No Apple Speech dependency — pure Parakeet path

## Obsidian sync (done)
- Summaries auto-copied to Obsidian vault after generation
- Destination: ~/Library/Mobile Documents/iCloud~md~obsidian/Documents/Transcriptions Summaries/
- Files named by session timestamp (e.g. 2026-03-29_14-30-00.md)

## Todoist integration
- Push [TASK] items to Todoist

## Transcript footer
- [x] Add session duration to summary.md footer (e.g. `· session: 42 min`)

## Startup mic selection
- [x] Warn on launch if Bluetooth device is active input (system_profiler + keyword fallback)
- [x] Input source selector in Ctrl+V menu — switch mic device mid-session

## Daily/weekly reporting
- [x] `scripts/report.py` — CLI aggregates sessions, syncs to Obsidian; `--today`, `--day`, `--this-week`, `--week YYYY-WNN`
- [x] Redesign report output: notable/brief classification (>=200 words), brief sessions collapsed, consolidated action items section, findall fix for multi-section action items

