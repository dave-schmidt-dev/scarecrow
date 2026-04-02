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
- [ ] Tune sys audio VAD thresholds with real meeting data
  - Replay tool: `python scripts/replay_test.py <wav> --save-baseline` / `--check-baseline`
- [ ] Wire up `EchoFilter.record_mic()` / `is_sys_echo()` — bidirectional suppression implemented but not called from app; evaluate whether sys-authoritative approach is sufficient first

## Launch-time audio source flags
- [x] `--mic-only` / `--sys-only` CLI flags to start with one source muted
- [x] Still configurable at runtime via existing mute toggles

## Level meter interaction
- [x] Left-click on mic/sys level meter to toggle mute
- [ ] Right-click on level meter opens context menu: mute toggle + VAD sensitivity adjustment
- [x] Keep Ctrl+M / Ctrl+Shift+S keyboard shortcuts

## Auto-segmentation for long sessions
- [ ] Automatically create segment boundaries at ~60 minute marks
- [ ] Each segment gets its own summary while maintaining full transcript continuity
- [ ] Supports 2-3 hour lectures/classes without manual splitting

## Summarizer model swap
- [x] Replace Nemotron-3-Nano with Gemma 3 27B IT (Google) for summarization
- [x] Nemotron had 57% failure rate: CoT leaking, repetition loops, unreliable structured output
- [x] Gemma 3 27B: best structured output compliance, 128K context, strong summarization benchmarks
- [x] Length-scaled prompts: short recordings get concise summaries, long sessions get comprehensive coverage

## Diarization
- Speaker identification/labeling in transcripts ("Speaker A", "Speaker B")
- Explore pyannote-audio or NeMo diarization models as a post-processing layer
- Would pair well with system audio for meeting transcription

## Auto-summarization (done)
- Local LLM summarization on shutdown via Gemma 3 27B IT (in-process via llama-cpp-python)
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

## Daily/weekly reporting
- Aggregate summaries across sessions

