# Scarecrow v1 Specification

## Summary

Scarecrow is a local transcription system for macOS that runs throughout the
day, records audio in durable chunks, shows live captions as a visual health
check, and supports later retrieval and summarization of past conversations.

This document describes the intended v1 behavior. It does not imply that the
implementation exists yet.

## Product Goals

- Capture audio continuously while the user is at the computer.
- Minimize data loss by saving audio in chunks.
- Use captions as an operational health check rather than a speculative preview.
- Persist recordings, transcripts, and summaries locally.
- Answer later questions using time context, transcript retrieval, and optional
  calendar context.
- Stay efficient by keeping heavier local models unloaded until needed.

## Non-Goals For v1

- Full automatic person-name speaker identification
- Native menu bar application
- Cloud-dependent transcription or summarization
- Calendar-required session labeling

## Core Behaviors

### 1. Chunked Recording

- Recording is continuous while Scarecrow is active.
- Audio is saved in short, rolling chunks.
- Chunks are written durably so the maximum expected loss is limited to the
  current in-flight chunk during a failure.

### 2. Caption Health Rules

- Live captions are shown only for chunks that were both:
  - recorded successfully
  - transcribed successfully
- If recording fails, transcription fails, or the pipeline health is unknown,
  the caption stream should stop rather than display misleading output.
- The TUI must surface health state clearly.

### 3. Persistence

- Audio recordings are saved locally.
- Transcripts are saved locally.
- Summaries are saved locally.
- Raw audio should be treated as shorter-retention data than transcripts and
  summaries.

### 4. Interaction Model

- The user launches Scarecrow with a single command: `scarecrow`.
- The primary interface is a terminal TUI.
- The TUI is responsible for:
  - showing captions
  - showing recorder/transcriber health
  - showing disk usage and warnings
  - accepting notes, markers, and summary requests

### 5. Session Context

- Calendar events can improve naming and later retrieval.
- Calendar context is optional and should never be required.
- Manual marks and notes must remain first-class inputs.

## Planned Technical Direction

- Local STT engine: `whisper.cpp`
- Local summary/query engine: Apple Silicon-optimized local LLM runtime
- Separate capture/transcription path from the heavier summary path
- A persistent store for:
  - session metadata
  - chunk metadata
  - transcripts
  - summary artifacts

## Storage Guidance

- Raw audio should eventually be pruned automatically.
- Disk usage should be shown when the TUI opens or closes.
- Scarecrow should warn when local storage exceeds a configured threshold.

## Speaker Handling

- v1 should support diarized speaker labels only.
- Full person-name recognition should be considered future work.
- The data model should leave room for future optional voiceprint enrollment.

## Open Implementation Priorities

1. Bootstrap repository and docs.
2. Define local config format and runtime directories.
3. Build recorder supervision and chunk persistence.
4. Add transcription pipeline and caption health gating.
5. Add TUI interaction model.
6. Add saved transcript and summary retrieval.
7. Add retention and disk usage reporting.
