# Roadmap

## System audio recording
- Capture system/app audio (meetings, calls, podcasts) in addition to mic input
- Combine system audio and mic audio into a single session for full meeting/call recording
- macOS options: BlackHole virtual audio device, Loopback, or ScreenCaptureKit API
- Could mix into one stream or record as separate channels

## Diarization
- Speaker identification/labeling in transcripts ("Speaker A", "Speaker B")
- Explore pyannote-audio or NeMo diarization models as a post-processing layer
- Would pair well with system audio for meeting transcription

## Auto-summarization (in progress)
- Local LLM summarization on shutdown via llama-server + Nemotron-3-Nano — implemented
- Prompt handles [NOTE], [TASK], [CONTEXT] tags — implemented
- Manual re-run via scripts/resummarize.py — implemented
- Needs end-to-end testing with real recordings
- Context window: 128K floor, 512K cap — no memory cost on short sessions (Mamba-2 linear scaling). Monitor token usage in summary footers to tune.
- Future: configurable model selection, streaming progress output

## Obsidian sync
- Push transcripts and summaries to an Obsidian vault

## Todoist integration
- Push [TASK] items to Todoist

## Daily/weekly reporting
- Aggregate summaries across sessions

