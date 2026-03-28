# Open Issues

## Parakeet backend (feature/parakeet-mlx branch)
- Parakeet does not support initial_prompt context injection — `/context` command logs to transcript but doesn't influence transcription accuracy
- LibriSpeech benchmark (3 min, normalized WER): parakeet 18.4% vs whisper 4.5% on chunked audio; parakeet is perfect (0% WER) on individual utterances — gap is from chunk boundary artifacts
- VAD silence threshold (0.01 RMS) may need tuning for noisy environments

## Transcript accuracy
- condition_on_previous_text=False on whisper batch path (True caused inference slowdowns)
- Monitor: "surge" → "search" type errors on clean podcast audio — may need domain-specific prompts

## Resource usage
- Whisper: 400% CPU mean, 3.6 GB RSS, ~4s per 15s chunk
- Parakeet: 50% CPU mean, 1.5 GB RSS, 2.2 GB GPU peak, ~50ms per chunk, <1W GPU power draw
- VAD chunking keeps GPU idle between speech pauses (~45mW idle)

## Accessibility
- Screen reader support blocked on Textual framework (planned but not yet shipped)
- Monitor Textual releases for accessibility API; integrate when available

## Setup script
- Needs testing end-to-end with a fresh clone

---

# Roadmap

## System audio recording
- Capture system/app audio (meetings, calls, podcasts) in addition to mic input
- macOS options: BlackHole virtual audio device, Loopback, or ScreenCaptureKit API
- Could mix or record separately from mic input

## Diarization
- Speaker identification/labeling in transcripts ("Speaker A", "Speaker B")
- Explore pyannote-audio or NeMo diarization models as a post-processing layer
- Would pair well with system audio for meeting transcription

## Auto-summarization
- End-of-session summary written to transcript directory
- Weight [NOTE] entries in body, list [TASK] items at end, use [CONTEXT] as background
- Bullet-point format

## Obsidian sync
- Push transcripts and summaries to an Obsidian vault

## Todoist integration
- Push [TASK] items to Todoist

## Daily/weekly reporting
- Aggregate summaries across sessions

## Branding
- Logo/emoticon — small SVG/PNG of a scarecrow inspired by Wizard of Oz
