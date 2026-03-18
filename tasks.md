# Scarecrow Tasks

Tracks implementation work against SPEC.md milestones. Each task maps to a
milestone and has acceptance criteria ("done when") and measurable validation
steps.

## Delivery Model

This file now serves two purposes:

1. Phase-level roadmap: what order to build in, why that order exists, and
   what gates must pass before advancing.
2. Milestone-level backlog: the concrete tasks and validation checks required
   to satisfy each phase.

The intent is to keep implementation work small, sequenced, and objectively
verifiable. A phase is not complete because code exists. A phase is complete
only when its exit gate passes.

## Phase Overview

| Phase | Name | Milestones | Goal | Exit Gate |
|-------|------|------------|------|-----------|
| P0 | Environment and Bootstrap | M1.1 only | Create a buildable Rust workspace, developer bootstrap path, and repo validation baseline | Clean local build, baseline validation command passes, repo structure is stable enough for parallel work |
| P1 | Core Foundation | Remaining M1 | Establish config, storage, status, recovery, logging, and first-run safety | Daemon can start safely, write state durably, and fail cleanly |
| P2 | Recording Pipeline | M2 + M4 | Capture audio reliably and classify it correctly before higher-level features | Continuous chunk capture works with correct routing and silence handling |
| P3 | Live Transcription Surface | M3 + M5 + M6.1 + M6.2 + M6.3a + M6.4 | Deliver the first usable operator experience: captions, health, notes, pause, lifecycle commands | User can run daemon/TUI daily and trust the basic recording loop |
| P4 | Cold-Path Intelligence | M7 | Add canonical transcripts, summaries, degraded modes, and worker lifecycle | Background processing is correct, bounded, and recoverable |
| P5 | Query and Recall | M8 | Make stored conversations retrievable through queries and markers | Queries return useful answers with provenance and acceptable latency |
| P6 | Operations and First-Run UX | M9 + M10 + M6.5 | Make the system maintainable for real use on a personal machine | Retention, setup, deletion, and disk warnings work safely |
| P7 | Hardening and Release Readiness | M11 | Prove resilience under long runs, crashes, and edge conditions | Soak, crash, concurrency, restart, and disk-full gates all pass |

## Phase Gates

### P0 — Environment and Bootstrap

**Goal:** turn the repository into a real build target with the minimum shared
crate structure needed for implementation.

**Entry criteria:**
- Toolchain available on the development machine (`cargo`, `rustc`, Python 3)
- Repository clean enough to scaffold without conflicting edits

**Build scope:**
- M1.1 only

**Exit gate:**
- Developer bootstrap documentation exists for a clean MacBook
- `cargo build` exits 0 for the workspace
- `cargo clippy --all-targets --all-features -- -D warnings` exits 0
- Canonical repo validation entrypoint exists and runs the current phase checks
- Binary crates and shared crate exist with initial ownership boundaries

**Do not start P1 until:**
- Workspace layout is stable enough that future milestones have a clear home

### P1 — Core Foundation

**Goal:** make the daemon safe to start, inspect, and recover before touching
real audio or model work.

**Entry criteria:**
- P0 exit gate passed

**Build scope:**
- M1.2 through M1.9

**Exit gate:**
- Config auto-creation and overrides behave as specified
- SQLite schema and FTS bootstrap succeed on a clean machine
- `scarecrow status` works without a running daemon
- Crash recovery, atomic `daemon.json`, and structured logging are verified
- Permission-denied microphone path is explicit and non-destructive

**Do not start P2 until:**
- The daemon can safely create, read, and recover its state on disk

### P2 — Recording Pipeline

**Goal:** produce trustworthy chunks and speech classification so downstream
features are operating on valid inputs.

**Entry criteria:**
- P1 exit gate passed
- Local machine has microphone access

**Build scope:**
- M2
- M4

**Exit gate:**
- Mic capture writes durable chunks continuously
- BlackHole routing works when available and degrades safely when absent
- Device changes and lock/unlock transitions are handled without undefined state
- Silence filtering and channel-aware routing match DB flags and file outcomes

**Do not start P3 until:**
- Chunk boundaries, audio retention decisions, and VAD outputs are trusted

### P3 — Live Transcription Surface

**Goal:** deliver the first real daily-driver slice: recording, captions, TUI,
notes, pause/resume, and operator health.

**Entry criteria:**
- P2 exit gate passed

**Build scope:**
- M3
- M5
- M6.1
- M6.2
- M6.3a
- M6.4

**Exit gate:**
- Draft transcripts are created for mic speech with latency validated by M3.2
- TUI connects/disconnects cleanly and shows captions plus baseline health
  states for mic, system audio, and transcription
- Lifecycle commands (`start`, `stop`, default TUI open) behave consistently
- Notes, pause/resume, local disk detail overlay, and help overlays work end
  to end

**Release value at exit:**
- First internal dogfood build
- Good checkpoint for early demo or architecture review

### P4 — Cold-Path Intelligence

**Goal:** turn draft capture into a canonical local record with worker-managed
background processing.

**Entry criteria:**
- P3 exit gate passed

**Build scope:**
- M7

**Exit gate:**
- Scheduled worker runs complete and record watermarks/status correctly
- Query preemption is validated as transaction-safe within the M7.1b SLA
- Canonical transcripts supersede drafts without stale FTS state
- Diarization and summaries work when enabled and degrade cleanly when disabled

**Do not start P5 until:**
- Canonical transcript generation is stable enough to serve retrieval

### P5 — Query and Recall

**Goal:** convert stored transcripts and markers into useful recall and summary
workflows.

**Entry criteria:**
- P4 exit gate passed

**Build scope:**
- M8

**Exit gate:**
- Time-window and marker-based queries return grounded answers against known
  transcript fixtures
- Query provenance is stored for debugging wrong answers later
- Warm worker follow-ups demonstrably reuse the same worker process
- TUI query states handle loading, errors, and follow-ups cleanly

**Release value at exit:**
- First feature-complete v1 behavior from the user perspective

### P6 — Operations and First-Run UX

**Goal:** reduce friction and risk for actual personal use on a new machine.

**Entry criteria:**
- P5 exit gate passed

**Build scope:**
- M9
- M10
- M6.5

**Exit gate:**
- Retention sweeps and disk warnings behave safely
- `scarecrow setup` can bootstrap a clean machine without manual file editing
- `delete-last` purges recent data correctly and predictably

**Do not start P7 until:**
- First-run and operational guardrails are in place

### P7 — Hardening and Release Readiness

**Goal:** prove the system is durable enough to trust for long-running local
capture.

**Entry criteria:**
- P6 exit gate passed

**Build scope:**
- M11

**Exit gate:**
- Full pipeline integration passes
- Crash recovery and supersession consistency are verified
- Soak test, concurrent writer safety, disk-full handling, and restart
  continuity all pass

**Release value at exit:**
- Candidate for public repo promotion and portfolio-grade demonstration

## Sequencing Rules

- Do not run multiple milestones in parallel until P0 and P1 are complete.
- Any task that changes persistent schema, IPC messages, or config format must
  update `SPEC.md` and `HISTORY.md` in the same change.
- A milestone is only marked complete when its `Validate:` checks are runnable,
  not merely implemented.
- Every phase gate must map to an explicit local validation command, query, or
  manual checklist item recorded in this file.
- If a phase exit gate fails, fix the gate failure before starting the next
  phase. Do not build around broken foundations.

## Recommended Near-Term Execution Order

1. P0: M1.1
2. P1: M1.2, M1.3, M1.6, M1.4, M1.7, M1.5, M1.9, M1.8
3. P2: M2.1, M2.2, M4.1, M4.2, M2.3, M2.4
4. P3: M3.1, M3.2, M5.1, M5.3, M5.2, M6.1, M6.2, M6.3a, M6.4
5. P4: M7.1a, M7.2, M7.5, M7.3, M7.4, M7.1b
6. P5: M8.1, M8.2, M8.3, M8.4
7. P6: M9.1, M9.2, M10.1, M6.5
8. P7: M11.1 through M11.6

## Definition of Done

A task or milestone is done only when all of the following are true:

- The implementation exists in the intended crate or module
- Validation steps have been run successfully on the local machine
- `./scripts/validate.sh` has been run and any relevant phase-specific checks
  have passed
- Documentation affected by the change is updated
- No known regressions were introduced into an earlier phase gate
- Remaining compromises are explicitly recorded as TODOs or in `HISTORY.md`

## Milestone Key

| Milestone | Name                       | Status  |
|-----------|----------------------------|---------|
| M1        | Foundation                 | planned |
| M2        | Audio Capture              | planned |
| M3        | Hot-Path Transcription     | planned |
| M4        | VAD and Silence Filter     | planned |
| M5        | IPC and TUI Basics         | planned |
| M6        | TUI Panels and Pause       | planned |
| M7        | Cold-Path Worker           | planned |
| M8        | Query Engine               | planned |
| M9        | Retention and Disk Mgmt    | planned |
| M10       | Setup Wizard and First Run | planned |
| M11       | Integration and Longevity  | planned |

---

## M1: Foundation

### M1.1 — Rust workspace scaffolding
- [ ] Add developer bootstrap documentation for a clean macOS machine
- [ ] Add canonical repo validation entrypoint at `./scripts/validate.sh`
- [ ] Create Cargo workspace with `scarecrow-daemon` and `scarecrow` binary crates
- [ ] Create `scarecrow-shared` library crate for types, IPC protocol, DB access
- [ ] Workspace compiles with `cargo build`
- **Validate:** Developer bootstrap doc is sufficient to install required
  local tools on a clean machine. `./scripts/validate.sh` exits 0 for the
  current repo state. `cargo fmt --check` exits 0. `cargo build` exits 0.
  `cargo clippy --all-targets --all-features -- -D warnings` exits 0. Both
  binary targets exist in `target/debug/`.

### M1.2 — Config parsing
- [ ] Define `scarecrow.toml` schema matching SPEC.md Configuration section
  (including `[audio]`, `[transcription]`, `[worker]`, `[llm]`, `[query]`,
  `[storage]`, `[logging]`)
- [ ] Parse config with defaults using `serde` + `toml` crate
- [ ] Create default config if none exists on first run
- [ ] Set 0600 permissions on created config file
- **Validate:** Run with no config file — default config is written to disk
  and matches SPEC.md defaults. Run with a custom config that overrides
  `chunk_duration_secs = 15` — verify parsed value is 15, not 30.
  Run with `[llm] backend = "llama-cli"` and `model_dirs = ["~/Models"]` —
  verify those values parse correctly and defaults for `cleanup_model`,
  `summary_model`, and `query_model` remain empty strings. Default
  `system_device` remains an empty string until BlackHole is configured.
  `stat -f %Lp scarecrow.toml` returns `600`.

### M1.3 — SQLite schema bootstrap
- [ ] Create `$SCARECROW_DATA/` directory with 0700 permissions
- [ ] Create `$SCARECROW_DATA/state/` directory with 0700 permissions
- [ ] Create database at `$SCARECROW_DATA/scarecrow.db` with 0600 permissions
- [ ] Apply all table schemas from SPEC.md Data Model section (chunks,
      transcripts, summaries, markers, pauses, cold_path_runs, queries)
- [ ] Set required pragmas: `journal_mode=WAL`, `busy_timeout=5000`,
      `wal_autocheckpoint=1000`, `foreign_keys=ON`
- [ ] Create FTS5 virtual table `transcripts_fts` on transcripts.text
- [ ] Create FTS5 virtual table `markers_fts` on markers.label
- **Validate:** Run once, then: `sqlite3 scarecrow.db "PRAGMA journal_mode"`
  returns `wal`. `PRAGMA busy_timeout` returns `5000`. Query
  `sqlite_schema` and verify the base tables `chunks`, `transcripts`,
  `summaries`, `markers`, `pauses`, `cold_path_runs`, and `queries` exist.
  Verify FTS virtual tables `transcripts_fts` and `markers_fts` exist.
  `SELECT * FROM transcripts_fts WHERE transcripts_fts MATCH 'test'` returns
  0 rows without error. `SELECT * FROM markers_fts WHERE markers_fts MATCH
  'test'` returns 0 rows without error. `stat -f %Lp scarecrow.db` returns
  `600`.
  `stat -f %Lp $SCARECROW_DATA` returns `700`.
  `stat -f %Lp $SCARECROW_DATA/state` returns `700`.

### M1.4 — Status command
- [ ] `scarecrow status` reads `daemon.json` and prints health summary
- [ ] Works without a running daemon (reads file directly)
- [ ] Reports "daemon not running" if no PID file or stale PID
- **Validate:** Create a mock `daemon.json` with known values. Run
  `scarecrow status` — output includes those values. Delete PID file, run
  again — output says "not running". Create PID file with non-existent PID,
  run again — output says "not running" (stale PID detected). Verify the PID
  file lives at `$SCARECROW_DATA/state/daemon.pid` and is not created outside
  the protected `state/` directory.

### M1.5 — Crash recovery and socket cleanup
- [ ] On startup, detect and clean up stale PID file (process not running)
- [ ] On startup, unlink stale `scarecrow.sock` and `worker.sock` if present
- [ ] On startup, scan `audio/` for orphaned chunk files (no matching DB row),
      delete them and log count
- [ ] On startup, close any open `pauses` row (`ended_at = NULL`) by setting
      `ended_at` to daemon's `updated_at` from `daemon.json`
- [ ] On startup, mark any `cold_path_runs` row with `status = 'running'` as
      `status = 'failed'` with error message noting daemon restart
- [ ] Register SIGTERM/SIGINT handler: flush current chunk to disk, write DB
      row, exit within 5-second grace period
- [ ] Register SIGQUIT handler: immediate exit
- **Validate:** Create stale PID file with non-existent PID — daemon starts
  cleanly, logs "cleaned up stale PID". Create stale `scarecrow.sock` file
  — daemon unlinks it and starts successfully. Create orphaned Opus file in
  `audio/` with no DB row — daemon deletes it on startup, logs "discarded 1
  orphaned chunk". Insert open `pauses` row — daemon sets `ended_at` on
  startup using `daemon.json` `updated_at`, logs recovery. Insert
  `cold_path_runs` row with `status = 'running'` — daemon updates to
  `'failed'` on startup. Send SIGTERM during recording — verify last chunk in
  DB has valid `ended_at` and audio file is complete (playable).

### M1.6 — daemon.json status file
- [ ] Write `daemon.json` matching SPEC.md Control Plane schema
- [ ] Atomic write: write to `daemon.json.tmp`, rename to `daemon.json`
- [ ] Update every 10 seconds and on every state change
- [ ] `updated_at` always set to current time on every write
- **Validate:** Start daemon, read `daemon.json` — valid JSON with all
  schema fields (including `worker_mode` as null/"cold-path"/"query").
  Wait 15 seconds, read again — `updated_at` has advanced.
  Pause recording — re-read within 2 seconds, `paused` is `true`. Kill daemon
  with SIGKILL (no graceful shutdown), read `daemon.json` — `updated_at` is
  the last write before kill (used by crash recovery). Write a corrupt
  `daemon.json.tmp`, verify `daemon.json` is still valid (atomic rename
  prevents partial reads).

### M1.7 — Structured logging
- [ ] Integrate `tracing` crate with JSON output formatter
- [ ] Log to `$SCARECROW_DATA/logs/scarecrow.log` with 0600 permissions
- [ ] Create `logs/` directory with 0700 permissions
- [ ] Implement log rotation: max `max_file_size_mb` per file, `max_files`
      retained
- [ ] Enforce log content policy: never log transcript text, marker labels,
      query content, or full audio file paths
- **Validate:** Start daemon, let it run for 1 minute. Log file exists and
  contains valid JSON lines. Each line has `timestamp`, `level`, and `message`
  fields. `grep` log for transcript text — no matches (content policy).
  `stat -f %Lp scarecrow.log` returns `600`. Create a 1 MB log file, set
  `max_file_size_mb = 0.001` — verify rotation creates a new file and old
  file is renamed. Set `max_files = 2`, create 3 rotated files — verify
  oldest is deleted.

### M1.8 — Model download and integrity verification
- [ ] On first daemon startup path, auto-download `tiny` model if missing
- [ ] Verify SHA256 checksum after download before loading
- [ ] Reject, delete, and retry if checksum does not match
- [ ] Cold-path model (`large-v3`) downloaded on first cold-path run, not
      at daemon startup
- **Validate:** Remove `tiny` model from `models/`. Trigger the daemon startup
  path that stages the hot-path model — model downloads and checksum is logged.
  Corrupt the model file (truncate it) — daemon rejects it and re-downloads.
  Place a file with wrong checksum — daemon rejects it and logs checksum
  mismatch. Actual runtime model load and inference readiness are validated in
  M3.1.

### M1.9 — macOS microphone permission handling
- [ ] On first run, handle macOS microphone permission prompt
- [ ] If denied, exit with clear error message and instructions
- [ ] Log permission status at startup
- **Validate:** Revoke mic permission in System Settings. Start daemon — exits
  with error message containing "System Settings > Privacy & Security >
  Microphone". Grant permission — daemon starts normally.

---

## M2: Audio Capture

### M2.1 — CoreAudio mic capture
- [ ] Capture audio from configured mic device via CoreAudio at native rate
- [ ] Write 30-second chunks as Opus files to `audio/YYYY-MM-DD/` directory
      with 0600 permissions in 0700 directories
- [ ] Double-buffer via ring buffer to prevent gaps at chunk boundaries
- [ ] Use `opusenc` CLI for v1 Opus encoding (shell out per chunk)
- **Validate:** Run daemon for 2+ minutes. Count Opus files — should be 4+
  for a 2-minute run. File sizes between 50 KB and 200 KB each.
  `stat -f %Lp` on an audio file returns `600`. Measure gap: compare
  `ended_at` of chunk N with `started_at` of chunk N+1 in DB — difference
  must be <10 ms for all adjacent pairs.

### M2.2 — BlackHole system audio capture
- [ ] Capture system audio from BlackHole device as channel 2
- [ ] Produce stereo Opus chunks (mic=ch1, system=ch2)
- [ ] Fall back to mono mic-only if BlackHole is unavailable
- [ ] Monitor system output device; set `sys_channel_healthy` in daemon.json
- [ ] Write `sys_channel_healthy` to each chunk row (TRUE if BlackHole routing
      was active during capture, FALSE if degraded)
- **Validate:** With BlackHole configured: play a known audio file through
  system audio while speaking. Extract channel 2 with `ffmpeg -map_channel
  0.0.1`, verify RMS amplitude is above -40 dBFS (non-silent). Extract channel
  1, verify it contains mic audio. `SELECT sys_channel_healthy FROM chunks
  ORDER BY id DESC LIMIT 1` returns TRUE. Without BlackHole: verify mono
  output, warning in log contains "mic-only". Switch system output away from
  Multi-Output Device — verify `daemon.json` shows `sys_channel_healthy: false`,
  log contains "System output changed", and new chunk rows have
  `sys_channel_healthy = FALSE`. Switch back — new chunks have
  `sys_channel_healthy = TRUE`.

### M2.3 — Device change handling
- [ ] Register CoreAudio device-change callbacks (via `coreaudio-rs`) for
      input AND output devices
- [ ] If configured mic is unavailable at startup, fall back to system default
      input and log a warning
- [ ] Log input device changes without interrupting mic capture
- [ ] Detect output device changes that break BlackHole routing
- [ ] Pause and warn if configured mic device is physically removed
- [ ] Implement `follow_system_default` config toggle for mic auto-switch
- **Validate:** Connect Bluetooth headset during recording — with
  `follow_system_default = false`, verify mic recording continues from
  configured device (check `input_device` in chunks table unchanged).
  Configure a missing mic device and start daemon — verify fallback to system
  default input with warning logged, recording still starts. Remove configured
  mic during runtime — verify warning logged and behavior matches
  `follow_system_default` (pause or switch to default). Change system output to
  built-in speakers — verify warning logged within 5 seconds and
  `sys_channel_healthy` flips to false. Change back — verify recovery logged
  and flag flips to true.

### M2.4 — Auto-pause on screen lock
- [ ] Monitor macOS screen lock/unlock notifications
- [ ] Auto-pause recording when screen locks (if `auto_pause_on_lock = true`)
- [ ] Auto-resume when screen unlocks
- [ ] Write `pauses` row for auto-pause intervals
- **Validate:** Lock screen — verify recording stops within 2 seconds, `pauses`
  row created. Unlock screen — recording resumes, `pauses` row has `ended_at`.
  Set `auto_pause_on_lock = false` — lock screen, verify recording continues.

---

## M3: Hot-Path Transcription

### M3.1 — whisper.cpp FFI integration
- [ ] Build whisper.cpp via `whisper-rs` crate, pin version
- [ ] Load `tiny` model at daemon startup, keep resident via `WhisperContext`
- [ ] Resample mic channel from 48 kHz to 16 kHz via `rubato` before inference
- [ ] Expose function to transcribe a single chunk's mic channel
- **Validate:** Check in a reference WAV file (`tests/fixtures/reference.wav`)
  with known transcript (`tests/fixtures/reference.txt`). Transcribe with
  `tiny` model. Compute WER using `jiwer` Python tool — must be <25%.
  Run 10 consecutive transcriptions, measure peak RSS via
  `/usr/bin/time -l` — must not exceed 200 MB. RSS delta over 10 runs
  must be <5 MB (no leak).

### M3.2 — Draft transcript pipeline
- [ ] After each chunk with `mic_has_speech`, run hot-path transcription
- [ ] Write draft transcript to SQLite with `tier='draft'`, `channel='mic'`,
      `is_current=TRUE`, `transcription_state='draft'` on chunk
- [ ] Insert draft text into `transcripts_fts` FTS5 index
- [ ] Measure and log latency per chunk
- **Validate:** Speak for 2 minutes. Draft transcripts appear in DB. Measure
  `created_at - chunk.ended_at` for each — must be <2 seconds for all chunks.
  Transcribe the reference fixture — output matches known transcript with
  WER <25%. FTS5 query for a known word from the reference returns the draft.

---

## M4: VAD and Silence Filtering

### M4.1 — Silero VAD integration
- [ ] Integrate Silero VAD via `ort` crate (ONNX Runtime)
- [ ] Handle LSTM hidden state correctly (reset at chunk boundaries)
- [ ] Require `silero_vad.onnx` in `$SCARECROW_DATA/models/` at startup; fail with a clear error and download instructions if the model file is missing
- [ ] Run VAD on both channels of each chunk independently (16 kHz mono each)
- [ ] Write `has_speech`, `mic_has_speech`, `sys_has_speech` to chunks table
- **Validate:** Record 30 seconds of silence, 30 seconds of speech, 30 seconds
  of system-channel spoken speech only, and 30 seconds of music on system
  audio only. Check DB: silence chunk has all speech flags FALSE. Mic speech
  chunk has `mic_has_speech = TRUE`. System spoken-speech chunk has
  `sys_has_speech = TRUE`, `mic_has_speech = FALSE`. Music-only chunk: VAD may
  or may not detect speech on the system channel (music can trigger VAD).
  Regardless of VAD outcome, the chunk is retained if `sys_has_speech = TRUE`
  and produces no hot-path transcript (mic is silent). If VAD does not detect
  speech on either channel, the chunk is treated as silent. The test must not
  assert a specific VAD outcome for music — only that the routing table is
  applied correctly given the actual VAD result.
  VAD wall-clock processing time (logged) must be <100 ms per chunk.

### M4.2 — Channel-aware routing
- [ ] Silent chunks (neither channel): discard audio, write metadata only
- [ ] Mic speech: send to hot-path transcription
- [ ] System-only speech: retain audio, skip hot-path, await cold path
- **Validate:** After a mixed session (silence + speech + music):
  `SELECT count(*) FROM chunks WHERE NOT has_speech AND audio_path IS NOT NULL`
  returns 0 (silent chunks have no audio). `SELECT count(*) FROM chunks WHERE
  sys_has_speech AND NOT mic_has_speech AND audio_path IS NOT NULL` returns >0
  (system-only chunks retained). `SELECT count(*) FROM transcripts WHERE
  chunk_id IN (SELECT id FROM chunks WHERE sys_has_speech AND NOT
  mic_has_speech)` returns 0 (no hot-path transcript for system-only chunks).

---

## M5: IPC and TUI Basics

### M5.1 — Unix socket IPC (daemon <-> TUI)
- [ ] Daemon listens on `$SCARECROW_DATA/state/scarecrow.sock` with 0600 perms
- [ ] Implement JSON + 4-byte length-prefix framing protocol
- [ ] Define message types in `scarecrow-shared` crate
- [ ] Enforce max message size (1 MB)
- [ ] Reject malformed or schema-invalid IPC payloads with logged warning
- [ ] TUI connects, receives caption stream and health updates
- [ ] Handle connection refused gracefully (daemon not running)
- **Validate:** Start daemon, connect TUI — captions flow. Kill TUI process,
  verify daemon continues (PID still alive, new chunks still written). Start
  TUI without daemon — error message within 2 seconds, no hang. Send a
  message exceeding 1 MB — daemon logs warning and drops it, does not crash.
  Send malformed JSON and schema-invalid JSON — daemon logs warning, drops the
  message, and stays healthy. `stat -f %Lp scarecrow.sock` returns `600`.

### M5.2 — TUI main view
- [ ] Scrolling live captions from daemon caption stream
- [ ] Baseline health bar with distinct indicators for:
      mic (recording/paused/lost), system audio (healthy/degraded/off),
      transcription (active/idle/error)
- [ ] Reserve UI slots for cold-path and disk states; full live states land in
      M7.1a and M9.2
- [ ] Graceful handling of daemon disconnect (message, no crash)
- **Validate:** Open TUI, speak for one full chunk — captions appear within 5
  seconds of that speech chunk completing. Kill daemon while TUI is open —
  TUI shows disconnect message, does not crash (exit code 0 or reconnect
  prompt). Health bar shows mic, system audio, and transcription categories.
  With BlackHole configured: system audio shows "healthy". Without: shows
  "off". Cold-path and disk sections may show placeholder/unavailable states
  until M7.1a and M9.2 are complete.

### M5.3 — Daemon lifecycle commands
- [ ] `scarecrow start` starts daemon in background, writes PID file
- [ ] `scarecrow stop` sends SIGTERM to daemon via PID file
- [ ] `scarecrow` (no subcommand) opens TUI connecting to running daemon
- **Validate:** `scarecrow start` — PID file created, `ps` shows process.
  `scarecrow start` again — error "already running". Open TUI, see captions.
  Close TUI — `ps` still shows daemon. `scarecrow stop` — PID file removed,
  process gone, last chunk in DB has valid `ended_at`.

---

## M6: TUI Panels and Pause

### M6.1 — Note panel
- [ ] `n` opens text input overlay
- [ ] On submit, write marker to SQLite with current timestamp and
      `marker_type = 'note'`
- [ ] Insert marker label into `markers_fts` FTS5 index
- [ ] Panel closes, return to main view
- **Validate:** Press `n`, type "starting call with Justin", submit. Query DB:
  `SELECT * FROM markers WHERE label = 'starting call with Justin'` returns
  1 row with `marker_type = 'note'` and `timestamp` within 2 seconds of wall
  clock time. `SELECT * FROM markers_fts WHERE markers_fts MATCH 'Justin'`
  returns 1 row. Press `Esc` in panel without typing — panel closes, no marker
  written.

### M6.2 — Pause/resume
- [ ] `p` sends pause command to daemon via IPC
- [ ] Daemon stops capture, writes `pauses` row with `started_at`
- [ ] `p` again resumes, updates row with `ended_at`
- [ ] TUI shows prominent PAUSED indicator
- [ ] `scarecrow pause` / `scarecrow resume` CLI commands
- **Validate:** Pause, wait 60 seconds, resume. Count chunks created during
  pause window — should be 0. `SELECT * FROM pauses ORDER BY id DESC LIMIT 1`
  — has both `started_at` and `ended_at`, interval is ~60s (±5s). Run
  `scarecrow pause` from second terminal without TUI — daemon stops recording
  (no new chunks). Run `scarecrow resume` — recording resumes.

### M6.3a — Query panel shell (pre-backend)
- [ ] `q` opens query text input
- [ ] Shows "query engine not yet available" placeholder response
- [ ] `Esc` closes panel
- **Validate:** Press `q` — panel appears. Type query, submit — placeholder
  message shown. Press `Esc` — panel closes, main view restored. No crash,
  no hang.

### M6.4 — Disk detail and help keybindings
- [ ] `d` opens disk usage detail overlay (breakdown by audio, DB, logs)
- [ ] `?` opens keybinding help overlay
- [ ] `Esc` closes either overlay
- **Validate:** Press `d` — disk detail shows a local snapshot of sizes for
  audio/, scarecrow.db, and logs/ even before live disk monitoring exists.
  Press `Esc` — overlay closes. Press `?` — help shows all implemented
  keybindings from SPEC.md. Press `Esc` — overlay closes.

### M6.5 — Delete recent recordings
- [ ] `scarecrow delete-last <duration>` CLI command (e.g., `5m`, `1h`)
- [ ] Hard-deletes every chunk whose time interval overlaps the specified window
- [ ] Deletes audio files plus chunk rows, transcript rows, and matching
      `transcripts_fts` entries for those chunks
- [ ] Deletes markers in the specified window and matching `markers_fts` entries
- [ ] Deletes summaries whose summary windows overlap the deleted window
- [ ] Deletes stored query rows whose resolved windows or provenance intersect
      deleted chunks or markers
- [ ] Trims or removes overlapping `pauses` rows so the deleted interval is not
      preserved in timeline metadata
- [ ] Does NOT set `audio_pruned`; retention and privacy purge remain separate
- [ ] Logs only counts and time range, never transcript, marker, or query text
- **Validate:** Record for 3 minutes. Run `scarecrow delete-last 2m`. Verify
  last 2 minutes of audio files are deleted. Overlapping `chunks` rows are
  gone, not marked `audio_pruned = TRUE`. Transcript rows for those chunks are
  deleted. `SELECT * FROM transcripts_fts WHERE transcripts_fts MATCH
  'known_deleted_term'` returns 0 rows. Markers created in the deleted window
  are gone from `markers` and `markers_fts`. Any `summaries` row whose
  `[window_start, window_end]` overlaps the deleted window is deleted.
  `queries` rows whose `window_start/window_end` overlap the deleted window or
  whose `transcript_ids` / `marker_ids` reference deleted rows are deleted.
  Query for deleted content returns no result or an explicit "no matching
  context" response. Grep the logs for `known_deleted_term` and deleted marker
  text — no matches. Remaining non-overlapping chunks are unaffected.
- **Validate (cold-path interaction):** If a cold-path run is in progress when
  `delete-last` runs, the daemon must either wait for the current chunk
  transaction to complete or reject the delete with a retry-after message.
  Validate: trigger `delete-last` while a cold-path run is active — verify no
  partial state, no worker crash, and the worker either continues from a valid
  watermark or exits cleanly.

---

## M7: Cold-Path Worker

### M7.1a — Worker lifecycle management
- [ ] Daemon spawns Python worker as subprocess with `--mode cold-path` arg
- [ ] Daemon implements cold-path scheduling timer (respects `cold_interval_mins`,
      0 disables batch scheduling)
- [ ] Daemon tracks worker PID, monitors for exit/crash
- [ ] Only one worker at a time
- [ ] `cold_path_runs` table updated with run status and watermark
- [ ] Create worker Python virtualenv at `$SCARECROW_DATA/venv/` if missing
- [ ] Discover GGUF models from configured `model_dirs` and build a local
      model catalog with validation status
- [ ] Persist the model catalog at `$SCARECROW_DATA/state/model_catalog.json`
- **Validate:** Trigger cold-path run. `SELECT * FROM cold_path_runs ORDER BY
  id DESC LIMIT 1` shows `status = 'completed'` with valid `processed_through`
  and `chunks_processed > 0`. Kill worker during run — `status` becomes
  `'failed'`, next scheduled run starts from last good watermark. Set
  `cold_interval_mins = 0` — verify no scheduled runs occur (only on-demand).
  Point `model_dirs` at a directory with known GGUF files — verify Scarecrow
  catalogs them and records healthy/unhealthy status after a smoke test in
  `$SCARECROW_DATA/state/model_catalog.json`.

### M7.1b — Query preemption and worker.sock
- [ ] Worker preemption via SIGUSR1 (flag checked between chunk transactions)
- [ ] Worker opens `worker.sock` Unix socket when entering query mode
- [ ] Daemon delivers query text to worker via `worker.sock`
- [ ] `worker.sock` enforces max message size and schema validation
- [ ] Preemption only acts between fully-committed chunk transactions
- **Validate:** Send query during cold-path run — worker checkpoints
  (`status = 'interrupted'`), opens `worker.sock`, transitions to query mode
  within 30 seconds. Verify `worker.sock` exists while in query mode, removed
  after exit. Send malformed and oversize query payloads — worker logs warning
  and drops them without crashing. Verify preemption safety: after interrupted
  run, every chunk with
  `transcription_state = 'canonical'` has a complete set of canonical
  transcript rows (mic + system + merged, all `is_current = TRUE`) — no
  partially-superseded chunks.

### M7.2 — Canonical transcription and FTS5 update
- [ ] Worker loads `large-v3` model, re-transcribes chunks since watermark
- [ ] Transcribes mic and system channels independently, produces merged output
- [ ] Applies confidence threshold to system channel transcripts
- [ ] Runs transcript cleanup/normalization with local `llama.cpp` GGUF model
      before writing canonical merged text
- [ ] Selects cleanup model by `cleanup_model` config first, then deterministic
      healthy discovered fallback
- [ ] Music deprioritization: flag extended sys-only activity without mic speech
- [ ] In a single transaction: sets `is_current=TRUE` on canonical rows,
      `is_current=FALSE` on superseded drafts, removes draft from
      `transcripts_fts`, inserts merged canonical into `transcripts_fts`,
      updates chunk `transcription_state` to 'canonical'
- **Validate:** After cold-path run: `SELECT count(*) FROM transcripts WHERE
  tier = 'canonical' AND channel = 'merged' AND is_current = TRUE` returns >0.
  `SELECT count(*) FROM transcripts WHERE tier = 'draft' AND is_current = TRUE
  AND chunk_id IN (SELECT id FROM chunks WHERE transcription_state =
  'canonical')` returns 0 (all drafts superseded). FTS5 search for a known
  spoken word returns the merged canonical transcript, not the draft. Verify
  no duplicate FTS5 hits for the same chunk. Feed low-confidence system-only
  music/media audio — verify it is flagged/deprioritized and does not dominate
  the merged canonical transcript or produce misleading queryable content.
  With explicit `cleanup_model`, verify the worker uses that model. With
  `cleanup_model = ""` and multiple healthy cleanup-capable candidates, verify
  deterministic fallback selection is logged and stable across runs.

### M7.3 — Diarization
- [ ] Integrate pyannote-audio for speaker labels via pluggable interface
- [ ] Graceful fallback if pyannote not configured (no speaker labels)
- [ ] Speaker labels stored as JSON in `speaker_labels` column on merged transcript
- **Validate:** With pyannote: `SELECT speaker_labels FROM transcripts WHERE
  channel = 'merged' AND is_current = TRUE LIMIT 1` — JSON is non-null and
  contains at least one `{speaker, start, end, text}` object. Without pyannote:
  same query returns NULL for `speaker_labels`, transcript `text` is still
  populated, no error in worker logs.

### M7.4 — Summary generation
- [ ] Load local `llama.cpp` GGUF model (sequential loading, not concurrent
      with whisper/pyannote)
- [ ] Invoke local models directly through `llama.cpp` (`llama-server` or
      `llama-cli`), not through `cclocal` or any other agent wrapper
- [ ] Selects summary model by `summary_model` config first, then deterministic
      healthy discovered fallback
- [ ] Generate summary for each cold-path processing window
- [ ] Include user marker text as context in summary prompt
- [ ] Sanitize marker text before prompt construction (strip control chars,
      truncate to 500 chars)
- [ ] Write summary to `summaries` table
- **Validate:** After cold-path run with speech: `SELECT count(*) FROM
  summaries WHERE window_end > [run_start_time]` returns >0. Summary `text`
  is non-empty and >50 characters. Add marker "meeting about budget" before
  run, but speak a distinct transcript fact such as "budget is fifteen
  thousand dollars". Verify the summary reflects the spoken fact, not just the
  marker label. Marker context may influence framing, but the summary must
  remain transcript-grounded. Worker logs show direct `llama.cpp` invocation,
  not an agent wrapper command. With explicit `summary_model`, verify that
  model is used. With `summary_model = ""` and multiple healthy
  summary-capable candidates, verify deterministic fallback selection is
  logged and stable across runs.

### M7.5 — Worker degraded modes
- [ ] `enable_summaries = false`: skip LLM loading and summary generation
- [ ] `enable_diarization = false`: skip pyannote loading, no speaker labels
- [ ] Both false: worker runs large-v3 only for canonical transcription
- [ ] Worker Python-side uses `structlog` for structured JSON logging
- **Validate:** Set `enable_summaries = false` — cold-path run produces
  canonical transcripts but no summaries. `SELECT count(*) FROM summaries`
  unchanged. Query panel shows "summaries and queries disabled" or equivalent.
  Submitting a query returns a clear message that query answering requires
  `enable_summaries = true`. Set `enable_diarization = false` — `speaker_labels` is NULL on
  all canonical transcripts. Set both false — worker peak RSS <4 GB (no LLM
  or pyannote loaded). Set `enable_diarization = false` only (summaries still
  enabled) — worker peak RSS <8 GB (large-v3 + LLM, no pyannote). Worker logs
  are valid JSON with `structlog` format.

---

## M8: Query Engine

### M8.1 — Time-window queries
- [ ] Worker accepts query text via `worker.sock`, resolves time references
      ("last hour", "2pm to 3pm")
- [ ] Retrieves all `is_current = TRUE` transcripts for the window (mixed
      draft mic + canonical merged is expected and acceptable)
- [ ] Sanitizes query text before LLM prompt construction
- [ ] Generates response via direct `llama.cpp` invocation using configured
      `query_model` or discovered fallback, not an agent wrapper
- [ ] Writes query + response + provenance to `queries` table (window_start,
      window_end, marker_ids, transcript_ids, transcript_tiers)
- [ ] Sends response back to daemon via `worker.sock`
- **Validate:** Speak known content ("the quarterly budget is fifteen thousand
  dollars"). After cold-path run, query "what was the budget number?" — response
  contains "fifteen thousand" or "15,000". `SELECT count(*) FROM queries` is
  incremented. `window_start` and `window_end` are non-null. `transcript_ids`
  JSON array is non-empty. Query again immediately after new speech (before
  cold path) — response still works using draft transcripts.
  `transcript_tiers` shows `{"draft": N}` for the recent portion. Worker logs
  show direct `llama.cpp` invocation using `query_model` or discovered fallback.

### M8.2 — Marker-based name queries with window boundaries
- [ ] Query engine searches `markers_fts` for name/keyword matches
- [ ] Uses matching marker timestamp as window start
- [ ] Resolves window end from: next marker, next pause, or
      `marker_window_max_mins` (whichever is earliest)
- [ ] Combines with transcript text for LLM response generation
- [ ] Stores matched marker IDs in `queries.marker_ids`
- **Validate:** Add marker "starting call with Justin" at time T. Speak for
  2 minutes. Add marker "done with Justin" at time T+3min. Query "summarize
  the call with Justin" — response covers transcript from T to T+3min (window
  bounded by next marker). `queries.marker_ids` contains the Justin marker ID.
  `queries.window_start` = T, `queries.window_end` = T+3min. Query "summarize
  the call with Sarah" (no marker) — response indicates no matching context.
  If `query_model` is unset but a healthy discovered summary/query-capable
  model exists, verify fallback selection is deterministic and logged.

### M8.3 — Worker warm mode for follow-ups
- [ ] After query, worker keeps `worker.sock` open for `idle_timeout_secs`
- [ ] Daemon routes follow-up queries to warm worker via `worker.sock`
- [ ] Worker exits and removes `worker.sock` after timeout with no new queries
- **Validate:** Ask query, note worker PID. Wait 10 seconds, ask follow-up —
  worker PID is the same (no restart). Check logs — no "loading model" entry
  between queries. Verify `worker.sock` exists between queries. Wait past
  `idle_timeout_secs` — worker PID is gone, `worker.sock` removed. Ask new
  query — new worker PID spawned, "loading model" appears in logs.

### M8.4 — Query panel UX states
- [ ] Spinner shown during model load and preemption transition
- [ ] Response displayed below query input in panel
- [ ] Follow-up queries possible without closing panel
- [ ] Error messages shown if worker fails
- **Validate:** Open query panel, submit query with no warm worker — spinner
  appears, response replaces spinner. Submit follow-up — no spinner (worker
  warm), response appears. Kill worker while spinner is showing — error
  message appears in panel, no crash.

---

## M9: Retention and Disk Management

### M9.1 — Audio retention sweep
- [ ] Daily sweep deletes audio files older than `audio_retention_days`
- [ ] Sets `audio_pruned = TRUE` on chunk rows (does NOT change `transcription_state`)
- [ ] Logs number of files deleted and space reclaimed in bytes
- [ ] Does NOT delete audio files currently being processed by cold-path worker
- **Validate:** Set `audio_retention_days = 0` for testing. Trigger sweep.
  Verify: audio files deleted from disk. `SELECT count(*) FROM chunks WHERE
  audio_pruned = TRUE` matches number of deleted files. `SELECT count(*) FROM
  chunks WHERE audio_pruned = TRUE AND transcription_state = 'canonical'`
  returns >0 (pruned but transcript preserved). Log entry shows file count
  and bytes reclaimed. Disk usage calculation excludes `models/` directory.

### M9.2 — Disk usage reporting
- [ ] Daemon monitors total data directory size (excluding models/)
- [ ] TUI displays disk usage in health bar
- [ ] Disk usage shown on TUI connect and disconnect
- [ ] Warning surfaced when usage exceeds `disk_warning_gb`
- **Validate:** Set `disk_warning_gb = 0.001` (1 MB) for testing. Start TUI
  — health bar shows disk usage in human-readable format (e.g., "42 MB").
  Warning indicator is visible. Set threshold back to 10 — warning disappears.
  Disconnect and reconnect TUI — the latest disk usage is surfaced on both
  lifecycle edges without crashing, and the reconnect view shows the same
  current calculation.

---

## M10: Setup Wizard and First Run

### M10.1 — `scarecrow setup` command
- [ ] Interactive wizard for first-time configuration
- [ ] Steps: mic permission check, BlackHole detection/setup guide (including
      sample rate and clock source verification), HuggingFace token for
      pyannote (via `huggingface-cli login`, with note about accepting terms
      on two model pages), Silero VAD ONNX model download and verification,
      GGUF model discovery/configuration, `llama-server` vs `llama-cli`
      backend selection, model pre-download option, cloud backup exclusion
      warning
- [ ] Writes config to `scarecrow.toml` with 0600 permissions
- [ ] Creates data directory with 0700 permissions
- [ ] Each optional step can be skipped with clear degraded-mode explanation
- [ ] Sets `com.apple.metadata:com_apple_backup_excludeItem` xattr on data dir
- **Validate:** Run on clean install with all components available — config
  file created with correct device names and `[llm]` settings. Wizard discovers
  GGUF files from default locations or lets the user choose model directories,
  writes `backend`, and validates a direct `llama.cpp` invocation against the
  selected cleanup/summary/query model path. Run again skipping BlackHole —
  config keeps `system_device = ""` and `multi_output_device = ""`, wizard
  prints "mic-only mode" explanation. Run again with empty model-role values —
  wizard writes empty strings and records discovered healthy candidates in the
  model catalog after smoke tests. Run again skipping pyannote — wizard prints
  "no speaker labels" explanation.
  Run `scarecrow start` after setup — daemon starts without errors.
  `xattr -l $SCARECROW_DATA` shows backup exclusion attribute.

---

## M11: Integration and Longevity

Cross-milestone validation that tests the full system under realistic
conditions. These tests run after all previous milestones are complete.

### M11.1 — Full pipeline integration test
- [ ] Record known audio (reference fixture) -> VAD classifies -> hot-path
      transcribes -> cold-path re-transcribes -> FTS5 updated -> query answered
- [ ] Verify each handoff: chunk in DB, draft transcript, canonical transcript
      supersedes draft, FTS5 returns canonical, query response references
      actual content
- **Validate:** Play reference audio through mic. After hot-path: draft
  transcript in DB, FTS5 returns it. Trigger cold-path. After: canonical
  merged transcript in DB, draft `is_current = FALSE`, FTS5 returns canonical
  only. Query "what was said?" — response references reference transcript
  content. Verify `transcript_tiers` in queries table.

### M11.2 — Crash during supersession transaction
- [ ] Kill worker at specific points during the supersession transaction
- [ ] Verify DB consistency after crash recovery
- **Validate:** Start cold-path run. Kill worker (SIGKILL) during processing.
  Restart daemon. Verify: no chunk has `transcription_state = 'canonical'`
  without a complete set of canonical transcript rows. No orphaned FTS5
  entries (FTS5 content matches `is_current` state). `cold_path_runs` row
  shows `'failed'`. Next cold-path run processes from last good watermark.

### M11.3 — Longevity / soak test
- [ ] Run daemon for 4+ hours continuously
- [ ] Monitor RSS, file descriptor count, WAL file size, log rotation
- [ ] Verify no resource leaks
- **Validate:** After 4 hours: RSS has not grown more than 20 MB from baseline.
  `lsof -p <pid> | wc -l` has not grown more than 10 FDs from baseline.
  WAL file size is <10 MB (checkpointing working). Log rotation has occurred
  at least once. All chunks in DB have valid timestamps. No gaps in timeline
  except explicit pauses.

### M11.4 — Concurrent writer safety
- [ ] Daemon and worker write to SQLite simultaneously
- [ ] Verify no SQLITE_BUSY errors propagate to user-visible failures
- **Validate:** Trigger cold-path run while daemon is actively recording.
  Both processes write chunks and transcripts concurrently. No errors in
  daemon or worker logs containing "SQLITE_BUSY" or "database is locked".
  All data is consistent after run completes.

### M11.5 — Disk-full handling
- [ ] Daemon handles ENOSPC gracefully when writing chunks
- [ ] Logs error, continues attempting on next chunk
- [ ] Does not crash or corrupt DB
- **Validate:** Fill the disk to near capacity. Start daemon — chunk write
  fails, error logged, daemon continues running. Free disk space — next
  chunk writes successfully. DB integrity check passes (`PRAGMA
  integrity_check` returns `ok`).

### M11.6 — Daemon restart continuity
- [ ] Record for 10 minutes, stop daemon, restart, record for 10 more minutes
- [ ] Cold path processes chunks from both sides of the restart
- [ ] Timeline has a clean gap (no overlap, no lost chunks)
- **Validate:** Stop and restart daemon. Trigger cold-path. Canonical
  transcripts cover chunks from both sessions. `pauses` table or chunk
  timestamps show a clean gap during downtime. Query spanning both sessions
  returns content from both.
