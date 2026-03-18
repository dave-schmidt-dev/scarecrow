# Scarecrow Decision History

## 2026-03-18 — Task planning restructured into phase gates

### Why
- The existing `tasks.md` had strong milestone-level detail, but it behaved
  like a backlog rather than a delivery plan. It was difficult to tell what
  needed to happen first, which milestones could be grouped into a usable
  slice, and what objective gate had to pass before moving forward.

### Decision
- Added a phase-based roadmap to `tasks.md` with:
  - P0 through P7 delivery phases
  - explicit goals, entry criteria, build scope, and exit gates
  - sequencing rules to prevent building on top of failing foundations
  - a recommended near-term execution order across milestones
  - a shared definition of done for tasks and milestones
- Tightened the roadmap after review to:
  - make phase gates more objective
  - fix sequencing contradictions between P3, M7, and M9
  - add developer bootstrap and validation-entrypoint requirements to P0
  - expand validation coverage for privacy deletion, IPC schema rejection,
    startup mic fallback, transcript cleanup, and summary grounding

### Outcome
- The repo now supports both detailed implementation tracking and higher-level
  delivery management. This should make it easier to execute on a new machine,
  stage work for portfolio-visible progress, and know when the project is
  ready for demos versus actual daily use.

## 2026-03-18 — Prefer `llama.cpp` for local cleanup and summaries

### Why
- The primary development machine now has `llama.cpp` installed.
- Using local GGUF models through `llama.cpp` keeps cleanup, summaries, and
  query answering fully local while reducing dependency sprawl relative to an
  MLX-specific path.

### Decision
- Prefer `llama.cpp` as the local LLM runtime for:
  - transcript cleanup and normalization after cold-path ASR
  - rolling summaries
  - query answering
- Product runtime should call `llama.cpp` directly (`llama-server` or
  `llama-cli`). `cclocal` remains a development convenience for manual prompt
  testing only and is not part of the Scarecrow runtime architecture.
- Model selection should be config-first, then auto-discovery from configured
  GGUF directories, then graceful degradation if no healthy local model exists.

### Outcome
- The planning docs now treat `llama.cpp` as the default local LLM runtime for
  Scarecrow's worker-side text generation tasks and document how model
  discovery and selection should work.

## 2026-03-14 — Project bootstrap and spec v1

### Initial decisions
- **Local-first transcription:** All processing happens on-device. No cloud
  dependencies for recording, transcription, or summarization.
- **Continuous timeline, no sessions:** The daemon runs all day. There are no
  discrete sessions — time windows and user-provided markers are the retrieval
  axes.
- **Three-process architecture:** Rust daemon (always running), Rust TUI
  (attach/detach), Python worker (short-lived for heavy processing).

### Audio format: Opus over FLAC
- Originally specified FLAC (lossless). Changed to Opus after reviewing storage
  implications. Opus is ~120 KB per 30-second chunk vs ~400 KB for FLAC. Since
  the audio exists for transcription (not archival listening), lossy compression
  at speech-optimized bitrates is sufficient. Whisper handles Opus without
  accuracy loss at these bitrates.

### Dual-channel capture over mic-only
- Originally specified mic-only capture. Changed to dual-channel (mic + system
  audio via BlackHole) to capture both sides of calls. Music/podcast filtering
  is handled by transcription confidence thresholds rather than per-app audio
  routing (which macOS doesn't support natively).

### Two-tier transcription
- Hot path: whisper.cpp `tiny` model, resident in memory, transcribes mic
  channel only for live captions. Fast (<2s per 30-second chunk).
- Cold path: whisper.cpp `large-v3` + pyannote diarization + local LLM
  summaries. Runs every 30 minutes. Produces canonical transcripts that
  supersede drafts.
- Originally specified `base` for the hot path. Changed to `tiny` after
  discovering that whisper.cpp `base` uses ~388 MB runtime memory (vs ~142 MB
  on disk). `tiny` uses ~125 MB runtime (~75 MB on disk). Since the hot path
  is a health-check for live captions and the cold path produces the record of
  truth, `tiny` is sufficient. `base` can be substituted if caption quality is
  too low, at the cost of ~200 MB additional RSS.

### Name-based retrieval via markers, not automatic speaker ID
- Automatic person-name speaker identification is a non-goal for v1. Instead,
  the user provides context markers ("starting call with Justin") via the TUI
  note panel. Name-based queries resolve through marker text matching. This is
  simpler, more reliable, and avoids the privacy/accuracy problems of
  voiceprint enrollment.

### Diarization: pyannote with gated access trade-off
- pyannote-audio is the best-in-class local diarization engine but requires
  accepting HuggingFace model license terms and providing an API token. The
  spec treats this as an optional enhancement — the system works without
  diarization, just without speaker labels.

### BlackHole Multi-Output Device caveats
- BlackHole Multi-Output Devices have known limitations: clock source must be
  the physical device (not BlackHole), sample rates must match, and Bluetooth
  disconnection can invalidate the aggregate device. The spec documents these
  and the daemon falls back to mic-only mode when the configuration is invalid.
- **Device switching is the biggest practical risk.** The Multi-Output Device
  does not automatically adapt when output changes (e.g., plugging in
  headphones creates a different device). AirPods/Bluetooth auto-connect will
  switch the system output away from the Multi-Output Device entirely, silently
  breaking BlackHole routing. Mitigation options include multiple profiles
  (one per output device) or a helper like SwitchAudioSource. For v1, the
  daemon detects the routing break and falls back to mic-only with a warning.
- Drift correction should be enabled on BlackHole (the virtual device), NOT on
  the hardware device that serves as clock source.

### Diarization engine selection
- **pyannote-audio** selected as primary. MIT license, best-in-class accuracy.
  Requires HuggingFace account and token (gated model access for contact
  collection, not license restriction). Both `speaker-diarization-3.1` and its
  segmentation dependency require separate license acceptance on HuggingFace.
- **SpeechBrain** identified as the strongest ungated alternative (Apache 2.0,
  models not gated on HuggingFace). If pyannote's gating becomes a friction
  problem, SpeechBrain should be evaluated. The worker's diarization interface
  is designed to be pluggable for this reason.
- **torch dependency:** pyannote requires PyTorch (~2 GB for torch alone). This
  is acceptable for the cold-path worker (short-lived, not resident) but means
  the worker virtualenv will be large. The `scarecrow setup` wizard should warn
  about download size on first run.

### Sample rate pipeline
- Audio devices capture at their native rate (typically 48 kHz). Resampling to
  16 kHz (what whisper.cpp expects) happens at inference time in the
  transcription layer, NOT at the Audio MIDI Setup level. Setting devices to
  16 kHz would degrade audio quality for other apps and is unnecessary —
  resampling a 30-second chunk is ~1 ms on Apple Silicon.

### Query preemption over queuing
- Originally specified that user queries queue behind scheduled cold-path runs.
  Changed to preemption: the daemon sends SIGUSR1 to the worker, which
  checkpoints its batch progress and transitions to query mode. This ensures
  interactive query latency is bounded by the checkpoint time (~10 seconds),
  not by the remaining duration of a batch run that could take 10+ minutes.

### Search surface: one transcript per chunk
- The cold path produces per-channel transcripts (mic, system) and a merged
  transcript. Only the merged canonical transcript (or the draft mic transcript
  before cold-path runs) is indexed in FTS5. This prevents duplicate search
  hits and gives the user a single coherent search surface.

### Chunk status split into orthogonal dimensions
- Originally used a single `status` column ('recorded', 'transcribed',
  'canonical', 'pruned') on chunks. This was overloaded — a chunk can be both
  'canonical' and pruned. Split into `transcription_state` ('pending', 'draft',
  'canonical') and `audio_pruned` (boolean). These are independent lifecycles.

### Worker IPC: Unix socket for live query dispatch
- Originally specified CLI args + shared SQLite as the only daemon-worker IPC.
  This breaks for warm-mode follow-up queries and post-preemption query
  delivery, because the worker is already running and can't receive new CLI
  args. Added a Unix socket (`worker.sock`) that the worker opens when it
  enters query mode. The daemon sends query text to this socket and receives
  responses. CLI args still used for initial mode selection; SQLite still used
  for persistent results.

### Calendar integration deferred from v1 schema
- Originally included a `calendar_events` table in the v1 schema as a
  placeholder. Removed because: no write path exists in v1, the speculative
  schema would likely need to change when the actual calendar API/sync
  mechanism is designed, and empty tables create the impression of a working
  feature. Calendar integration will introduce its own schema when designed.

### Marker type simplified to single value
- Originally defined `marker_type` as 'note' or 'context'. Removed 'context'
  because there is no user-facing mechanism to distinguish the two — all
  markers are entered through the same note panel. The text itself provides
  context. If different marker behaviors emerge later, the type can be
  extended.

### FTS5 index maintenance made explicit in supersession
- The transcript supersession transaction now explicitly includes FTS5 index
  operations: deleting the draft row from `transcripts_fts` and inserting the
  merged canonical row, in the same transaction as the `is_current` flag
  updates. Without this, stale draft text would remain searchable alongside
  canonical text.

### Worker resource budget and degraded modes
- The cold-path worker can peak at ~6-9 GB RAM (large-v3 + pyannote + LLM).
  This requires 16 GB unified memory for comfortable operation. Added explicit
  minimum system requirements (16 GB recommended, 8 GB works in degraded mode)
  and three degraded modes: skip LLM summaries, skip diarization + LLM, or
  skip the cold path entirely. Config flags `enable_summaries` and
  `enable_diarization` control this.

### Marker window boundary rules
- Marker-based queries previously had no contract for where the window ends.
  "Summarize the call with Justin" was ambiguous. Added explicit boundary
  rules: the window extends from the marker to the earliest of (next marker,
  next pause, or `marker_window_max_mins` default 60 minutes). Extended
  silence (>5 min no speech) is a soft hint but hard boundaries take
  precedence.

### System audio health persisted per chunk
- Added `sys_channel_healthy` boolean to the chunks table. This distinguishes
  "channel 2 was silent because nobody was talking" from "channel 2 was silent
  because BlackHole routing was broken." Without this, the cold path cannot
  tell whether system-audio silence is meaningful or a capture failure.

### daemon.json schema and atomic write
- daemon.json was referenced throughout the spec but had no defined schema,
  write cadence, or atomicity rule. Added explicit JSON schema, 10-second
  write cadence plus state-change writes, and atomic write via temp-file
  rename. Also added stale socket cleanup to crash recovery.

### Query provenance enriched
- The queries table originally stored only question, response, model version,
  and timestamp. Added `window_start`, `window_end`, `marker_ids`,
  `transcript_ids`, and `transcript_tiers` to enable debugging wrong answers
  after the fact. Also clarified that queries answer from mixed draft/canonical
  state without triggering canonicalization.

### Preemption safety: between-transaction boundary
- The worker's SIGUSR1 preemption handler sets a flag but only checks it
  between chunk processing transactions — after the current chunk's
  supersession transaction (FTS5 update, is_current flip) has fully committed.
  This guarantees `processed_through` always points to a chunk whose
  transaction is complete. Without this rule, an interrupted supersession
  could leave a chunk in a partially-updated state.

### Preemption SLA adjusted from 10s to 30s
- Originally specified 10-second preemption target. Changed to 30 seconds
  after implementation review: whisper.cpp `large-v3` inference on a single
  30-second chunk can take 10-30 seconds on Apple Silicon CPU, and Python's
  signal handler doesn't fire during C extension calls (torch, whisper).
  The flag is only checked between chunks, so worst case is completing one
  full chunk inference before preempting.

### Security and file permissions
- Added a Security and File Permissions section to the spec defining: threat
  model (single-user macOS), file permissions (0700 dirs, 0600 files), log
  content policy (never log transcript text), cloud backup exclusion, IPC
  message size limits (1 MB), model integrity verification (SHA256 checksums),
  and HuggingFace token storage (standard HF cache only, not in scarecrow.toml).

### Privacy enhancements: auto-pause and delete-last
- Added auto-pause on screen lock/lid close (enabled by default). The daemon
  monitors macOS lock notifications and pauses recording automatically.
- Added `scarecrow delete-last <duration>` command for purging recent
  recordings. Safety valve for accidental capture of sensitive content.

### Implementation technology choices
- IPC serialization: JSON with 4-byte big-endian length-prefix framing.
  Chosen for debuggability and simplicity. Performance is irrelevant at the
  message rates involved.
- SQLite pragmas: `busy_timeout = 5000` is essential for multi-process writer
  safety. `wal_autocheckpoint = 1000` prevents WAL file growth.
- Opus encoding: shell out to `opusenc` CLI for v1. Pure-Rust OggOpus
  container construction is tedious; process-spawn overhead is negligible for
  30-second chunks.
- Recommended crates: `whisper-rs` for FFI, `ort` for Silero VAD ONNX,
  `cpal`+`coreaudio-rs` for audio, `rubato` for resampling, `ringbuf` for
  lock-free audio buffering, `ratatui` for TUI.
- `mlx-lm` for local LLM inference (Python). Sequential model loading is
  mandatory — do not load whisper + pyannote + LLM concurrently.

### Integration and longevity testing (M11)
- Added M11 milestone for cross-milestone integration tests: full pipeline
  end-to-end, crash during supersession transaction, 4-hour soak test,
  concurrent writer safety, disk-full handling, and daemon restart continuity.
  These catch the class of bugs that individual milestone tests miss.
