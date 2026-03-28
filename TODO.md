# Open Issues

## Transcript accuracy
- VAD silence threshold (0.01 RMS) may need tuning for noisy environments
- LibriSpeech benchmark (3 min, normalized WER): parakeet 18.4% on chunked audio; parakeet is perfect (0% WER) on individual utterances — gap is from chunk boundary artifacts

## Accessibility
- Screen reader support blocked on Textual framework (planned but not yet shipped)
- Monitor Textual releases for accessibility API; integrate when available

## Setup script
- Needs testing end-to-end with a fresh clone

---

# Roadmap

## Parakeet model investigation
- Investigate alternative parakeet models for improved accuracy
- Compare WER across different model sizes (0.6B vs larger variants)
- Evaluate accuracy/speed/memory tradeoffs on M5 Max hardware
- Test with domain-specific audio (meetings, podcasts, dictation)

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
- Weight [NOTE] entries in body, list [TASK] items at end
- Bullet-point format

## Obsidian sync
- Push transcripts and summaries to an Obsidian vault

## Todoist integration
- Push [TASK] items to Todoist

## Daily/weekly reporting
- Aggregate summaries across sessions

## Branding
- Logo/emoticon — small SVG/PNG of a scarecrow inspired by Wizard of Oz
