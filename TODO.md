# Roadmap

## System audio (Phase 1 + 2 done)
- [x] Capture system audio via BlackHole to separate WAV (on by default, `--no-sys-audio` to disable)
- [x] Dual InfoBar level meters (mic + sys)
- [x] Streaming FLAC compression on shutdown
- [x] Auto-switch to Scarecrow Output on startup, restore on exit
- [x] Transcribe both channels via Parakeet (shared executor, mic priority)
- [x] Left-aligned mic, right-aligned italic sys transcripts in TUI
- [x] JSONL `"source": "mic"|"sys"` field on transcript events
- [x] Per-source mute (Ctrl+M mic, Ctrl+Shift+S sys)
- [x] Tune sys audio VAD thresholds with real meeting data
  - Benchmarked via `replay_test.py --save-reference` / `--compare-reference` against ITN101 class (60 min sys audio)
  - Swept silence threshold, min silence duration, and min buffer seconds
  - Results: `benchmarks/vad_tuning_2026-04-05.md`
  - Changes: threshold 0.003→0.001, silence 750→1500ms, buffer 5→8s (seq match 0.911→0.932, drains 608→287)
- [ ] Fine-tune sys VAD with smaller intervals around current targets
  - Threshold: sweep 0.0005–0.002 in 0.00025 steps (current: 0.001)
  - Min silence: sweep 1250–1750ms in 125ms steps (current: 1500)
  - Min buffer: sweep 6–10s in 1s steps (current: 8)
  - Test against additional recordings for generalizability
- [x] Wire up `EchoFilter.record_mic()` / `is_sys_echo()` — bidirectional suppression wired in app.py (2026-04-02)

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

## TurboQuant KV-cache compression
- [ ] Evaluate TurboQuant (mlx-vlm `kv_bits` parameter) for summarizer inference
  - Could reduce memory pressure for Gemma 4 26B during summarization
  - `_MlxBackend` already accepts `kv_bits` from config (`SUMMARIZER_MLX_KV_BITS`)
  - Test summary quality at kv_bits=4 and kv_bits=8 vs baseline (no compression)

## Summarizer model swap
- [x] Replace Nemotron-3-Nano with Gemma 3 27B IT (Google) for summarization
- [x] Nemotron had 57% failure rate: CoT leaking, repetition loops, unreliable structured output
- [x] Gemma 3 27B: best structured output compliance, 128K context, strong summarization benchmarks
- [x] Length-scaled prompts: short recordings get concise summaries, long sessions get comprehensive coverage
- [x] Replace Gemma 3 27B (GGUF) with Gemma 4 26B MoE (MLX) — 8x faster, 2.4x less RAM, comparable quality
- [x] Remove SUMMARIZER_MIN_CTX (128K floor) — dynamic sizing handles this correctly

## Diarization
- Speaker identification/labeling in transcripts ("Speaker A", "Speaker B")
- Explore pyannote-audio or NeMo diarization models as a post-processing layer
- Would pair well with system audio for meeting transcription

## Auto-summarization (done)
- Local LLM summarization on shutdown via Gemma 4 26B MoE (MLX default, GGUF fallback)
- Prompt extracts: executive summary, key points, action items (explicit [TASK] + implicit follow-ups)
- Handles [NOTE], [TASK], [CONTEXT] tags
- Auto-syncs summaries to Obsidian vault
- Manual re-run via scripts/resummarize.py

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

