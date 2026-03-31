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

## Diarization
- Speaker identification/labeling in transcripts ("Speaker A", "Speaker B")
- Explore pyannote-audio or NeMo diarization models as a post-processing layer
- Would pair well with system audio for meeting transcription

## Auto-summarization (done)
- Local LLM summarization on shutdown via Nemotron-3-Nano (in-process via llama-cpp-python)
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

