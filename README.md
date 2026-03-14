# Scarecrow

Scarecrow is a local-first, always-available transcription companion for macOS.
It is intended to run during the workday, capture audio in durable chunks, show
live captions as a health check, and let the user ask questions about prior
conversations without sending recordings or transcripts to cloud services.

## Status

Planning and repository bootstrap. No application code has been implemented yet.

## Goals

- Save audio in short chunks so recorder failures lose as little data as possible.
- Show live captions only when recording and transcription are both healthy.
- Persist recordings, transcripts, and summaries locally.
- Support later questions such as "summarize the call I had with Mike and Justin earlier this afternoon."
- Stay lightweight when idle and load heavier local models only when needed.

## Planned Architecture

- A single user-facing command: `scarecrow`
- A background daemon for capture, chunking, and supervision
- A terminal TUI for live captions, status, and interaction
- `whisper.cpp` for speech-to-text
- An Apple Silicon-optimized local LLM path for summaries and semantic queries
- Optional calendar context that improves retrieval but is never required

## Reliability Rules

- Audio is written in durable chunks.
- Captions are derived only from successfully recorded and successfully transcribed chunks.
- If recording or transcription is unhealthy, caption streaming stops and the UI shows degraded health.
- Heavy models should not stay loaded when they are not actively needed.

## Planned Documentation

- `README.md`: project overview and direction
- `SPEC.md`: v1 product and implementation plan
- `.gitignore`: local/runtime exclusions

## Notes

The current working directory may still be named `office_brain` locally, but the
project itself is `Scarecrow`.
