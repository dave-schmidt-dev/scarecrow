# Scarecrow v1 Specification

## Summary

Scarecrow is a local transcription system for macOS that runs throughout the
day, records audio in durable chunks, shows live captions as a visual health
check, and supports later retrieval and summarization of past conversations.

This document describes the intended v1 behavior. It does not imply that the
implementation exists yet.

## Product Goals

- Capture audio continuously while the user is at the computer.
- Capture both microphone input and system audio for full call coverage.
- Minimize data loss by saving audio in chunks.
- Use captions as an operational health check rather than a speculative preview.
- Persist recordings, transcripts, and summaries locally.
- Answer later questions using time context, transcript retrieval, and
  user-provided markers.
- Stay efficient by keeping heavier local models unloaded until needed.

## Non-Goals For v1

- Full automatic person-name speaker identification (names come from user
  markers, not automatic recognition)
- Native menu bar application (macOS orange mic dot serves as visual indicator)
- Cloud-dependent transcription or summarization
- Calendar integration (deferred to a future version)
- Per-app audio routing (system audio is captured wholesale; music filtering
  is handled by the transcription pipeline, not audio routing)
- Auto-start at login (user starts manually)
- Obsidian or other note app integration

## Process Architecture

Scarecrow runs as three cooperating processes.

### scarecrow-daemon (Rust) — always running

The daemon is the core recording and transcription engine. It runs headless and
does not depend on the TUI or worker being connected/running.

Responsibilities:
- Dual-channel audio capture (mic + system audio via BlackHole)
- Chunk writing and rotation
- Hot-path transcription (whisper.cpp via FFI, `tiny` model, kept resident)
- VAD-based silence detection and chunk classification
- SQLite writes for all persistent data
- Periodic cold-path worker scheduling and lifecycle management
- Unix socket server for TUI connections
- Status file at `$SCARECROW_DATA/state/daemon.json` for `scarecrow status`
- Structured logging with rotation to `$SCARECROW_DATA/logs/`
- Pause/resume via IPC command (triggered by TUI hotkey or CLI)

Resource targets:
- Idle RSS: <200 MB (dominated by resident whisper `tiny` model at ~125 MB
  runtime memory, plus CoreAudio buffers and SQLite)
- CPU between transcriptions: <5%
- The whisper `tiny` model stays loaded continuously. No unload/reload cycle,
  no audio gap between chunks.

**Why `tiny` and not `base`:** whisper.cpp `base` requires ~388 MB of runtime
memory despite being only ~142 MB on disk. `tiny` uses ~125 MB at runtime
(~75 MB on disk). Since the hot path is a health check for live captions — not
the record of truth — `tiny` is sufficient. The cold path with `large-v3`
produces the canonical transcripts. If `tiny` proves too inaccurate for usable
captions, `base` can be substituted at the cost of ~200 MB additional RSS.

### scarecrow (Rust TUI) — attach/detach at will

The TUI connects to the daemon over the Unix socket. It can attach and detach
freely without affecting recording. The daemon does not require a TUI connection
to function.

Responsibilities:
- Live caption display (main view)
- Recorder, transcriber, and disk health indicators
- Modal panels triggered by keybindings (see TUI Interaction Model)
- `scarecrow status` subcommand that reads daemon status file directly (works
  without a running TUI or daemon socket)

### scarecrow-worker (Python) — short-lived, spawned by daemon

The worker is invoked by the daemon as a subprocess, either on a schedule
(cold-path processing) or on demand (user queries). It loads heavy models,
does work, and exits. It is never resident long-term.

Responsibilities:
- Re-transcription with `large-v3` whisper model for accuracy
- Transcript cleanup and normalization via `llama.cpp`
- Speaker diarization (see Diarization Engine section)
- Summary generation via direct `llama.cpp` invocation
- Answering user queries against transcript history via direct `llama.cpp`
- Writes canonical transcripts, summaries, and query responses back to SQLite

Resource requirements (peak, during cold-path run with all features):
- whisper.cpp `large-v3`: ~3 GB RAM
- pyannote diarization pipeline: ~300 MB RAM
- Local LLM (7B quantized, e.g., Mistral-7B-Q4): ~4-5 GB RAM
- Python + torch overhead: ~500 MB RAM
- **Total peak: ~8-9 GB RAM** (models are loaded sequentially, not all at once,
  so actual peak depends on implementation — sequential loading reduces this
  to the largest single model plus overhead, roughly ~6 GB)

**Minimum system requirements for full cold-path functionality:**
- 16 GB unified memory (Apple Silicon). With the daemon at <200 MB plus the
  worker peak, 16 GB provides adequate headroom for macOS and other apps.
- ~10 GB free disk for models (`large-v3` ~3 GB, LLM ~4-5 GB, pyannote
  ~300 MB, `tiny` ~75 MB).

**Degraded modes for 8 GB systems:**
- **Skip LLM summaries:** Set `[worker] enable_summaries = false` in config.
  Worker runs `large-v3` + pyannote only (peak ~4 GB). Canonical transcripts
  and diarization work. Summaries and query answering are disabled.
- **Skip diarization + LLM:** Set `[worker] enable_diarization = false` and
  `enable_summaries = false`. Worker runs `large-v3` only (peak ~3.5 GB).
  Canonical transcripts work. No speaker labels, no summaries, no queries.
- **Skip cold path entirely:** Set `cold_interval_mins = 0`. The daemon never
  spawns the worker for scheduled batch processing. Only the hot-path `tiny`
  model runs. Draft transcripts only, no canonical re-transcription. On-demand
  queries still work (the daemon spawns a worker on request). Minimum viable
  mode for very constrained systems.

Lifecycle:
- **Scheduled run:** Daemon spawns worker on cold-path interval. Worker
  processes backlog, writes results, exits immediately.
- **Query run:** Daemon spawns worker on user query. Worker processes query,
  then stays warm for `idle_timeout_secs` (default 300s) to handle follow-up
  queries. If no query arrives within the timeout, worker exits.
- **Query preemption:** If a user query arrives while a scheduled cold-path
  run is in progress, the daemon sends the worker a preempt signal (SIGUSR1).
  The worker sets an internal preemption flag but does NOT act on it
  mid-transaction. The flag is checked only between chunk processing
  transactions — i.e., after the current chunk's supersession transaction
  (FTS5 update, `is_current` flip, `transcription_state` update) has fully
  committed. This guarantees `processed_through` always points to a chunk
  whose transaction is complete, never a partially-updated one. The worker
  then checkpoints its progress (updates `cold_path_runs.processed_through`
  to the last fully-committed chunk, sets `status = 'interrupted'`), and
  transitions to query mode. The next scheduled run resumes from the
  checkpoint. This ensures interactive queries are never blocked behind a
  long batch run.
- The daemon tracks worker PID and state. Only one worker process runs at a
  time.

IPC between daemon and worker:
- **Startup:** The daemon spawns the worker with command-line arguments
  specifying the initial mode (`--mode cold-path` or `--mode query`) and the
  database path. The worker opens the SQLite database for reading input and
  writing results.
- **Live query dispatch:** The worker listens on a Unix domain socket at
  `$SCARECROW_DATA/state/worker.sock` once it enters query mode (either
  directly or after preemption). The daemon sends query text to this socket.
  The worker writes responses back to the socket AND to the `queries` table.
  This socket is the mechanism for delivering follow-up queries to a warm
  worker and for delivering the initial query after preemption.
- **Health monitoring:** The daemon monitors worker health via PID and exit
  code. If the worker crashes, the daemon logs the failure, cleans up the
  worker socket, and retries on the next scheduled interval.

## Core Behaviors

### 1. Chunked Recording

- Recording is continuous while the daemon is active. There are no discrete
  sessions — the daemon runs all day and chunks form a continuous timeline.
- Audio is saved in 30-second chunks as Opus (lossy, optimized for speech,
  ~120 KB per chunk at 64 kbps mono equivalent).
- Chunks are stereo: channel 1 is microphone, channel 2 is system audio.
- Chunks are written durably so the maximum expected loss is limited to the
  current in-flight chunk during a failure.
- The whisper `tiny` model stays resident in memory. Chunk boundaries are
  handled by double-buffering: the next chunk's capture starts before the
  current chunk's file is finalized. No audio is lost at boundaries.

### 2. Silence Detection and Filtering

- Every chunk is run through Silero VAD (Voice Activity Detection) in the hot
  path, immediately after capture.
- VAD runs on both channels independently.
- A chunk is considered silent only if neither channel has detected speech.
  Silent chunks have their audio file discarded immediately — only a metadata
  row is kept noting the silent period.
- Chunks with speech on either channel are retained on disk.

#### Hot-Path Routing by Channel

| mic_has_speech | sys_has_speech | Action                                        |
|----------------|----------------|-----------------------------------------------|
| true           | true or false  | Hot-path transcribes mic channel. Captions shown. |
| false          | true           | Chunk retained on disk. **No hot-path transcription, no captions.** Cold path will process both channels later. |
| false          | false          | Silent. Audio discarded. Metadata row only.   |

This means chunks where only the system channel has speech (e.g., remote caller
talking while user is muted, or media playback) are saved but do not produce
live captions. The cold path handles them.

### 3. Music and Non-Speech Filtering

- System audio (channel 2) captures everything: call audio, music, podcasts,
  video, notifications.
- Music and podcast audio is not filtered at the audio capture level. Instead,
  the transcription pipeline handles it:
  - **Hot path:** Transcribes mic channel only. Music on system audio does not
    affect captions.
  - **Cold path:** Transcribes both channels. Music produces either silence,
    low-confidence garbage, or lyric fragments. The cold-path worker applies a
    confidence threshold and discards low-quality segments.
  - **Heuristic:** System audio activity without corresponding mic activity
    (user not speaking) over extended periods is likely music/media, not a
    call. The cold path can flag these segments for deprioritization.
- This approach avoids the impossible problem of per-app audio routing on macOS
  and instead lets transcription quality be the filter.

### 4. Caption Health Rules

- Live captions are shown only for chunks that were both:
  - recorded successfully
  - transcribed successfully by the hot-path engine (mic channel)
- If recording fails, transcription fails, or the pipeline health is unknown,
  the caption stream should stop rather than display misleading output.
- The TUI surfaces health state clearly. When the TUI is not connected, the
  daemon writes health to `daemon.json` and the macOS orange microphone
  indicator dot shows that recording is active.

### 5. Two-Tier Transcription

#### Hot Path (continuous, Rust)

- Engine: whisper.cpp via FFI
- Model: `tiny` (~75 MB on disk, ~125 MB runtime RAM), kept resident
- Input: mic channel only (channel 1), resampled to 16 kHz before inference
- Trigger: only when `mic_has_speech` is true for the chunk
- Produces draft transcripts used for live captions
- No diarization — single-speaker output only
- Latency target: <2 seconds per 30-second chunk on Apple Silicon (`tiny` is
  faster than `base`)

#### Cold Path (periodic or on-demand, Python worker)

- Schedule: every 30 minutes (configurable), or on-demand for user queries
- Engine: whisper.cpp `large-v3` model (~3 GB) for transcription accuracy
- Input: both channels, resampled to 16 kHz before inference, processed
  independently then merged
- Diarization: speaker labels (see Diarization Engine section)
- Transcript cleanup and normalization: local GGUF model via `llama.cpp`
- Summary: local GGUF model via `llama.cpp`
- Invocation path: direct `llama.cpp` process integration (`llama-server` or
  `llama-cli`), not an agent wrapper or editor/CLI assistant alias
- Confidence filter: segments below threshold on system audio channel are
  discarded (catches music, notification sounds, etc.)
- Re-processes all chunks since the last completed or interrupted cold-path run
  (tracked by `cold_path_runs.processed_through` watermark)
- Produces canonical merged transcripts that supersede draft versions (see
  State and Provenance section)
- Generates rolling summaries for the processed window
- Incorporates user-provided markers/notes from the processing window as
  context in summary prompts
- Can be preempted by user queries (see Process Architecture, query preemption)

### 6. Persistence

- Audio recordings (Opus) are saved locally under `$SCARECROW_DATA/audio/`.
- Draft transcripts (hot path) and canonical transcripts (cold path) are stored
  in SQLite.
- Summaries are stored in SQLite.
- Manual notes and markers are stored in SQLite.
- Query history and responses are stored in SQLite.
- Raw audio is pruned automatically after 14 days.
- Transcripts and summaries are retained indefinitely.

### 7. TUI Interaction Model

The TUI has a main view and modal panels that pop up via keybindings.

#### Main View

- Scrolling live captions (from hot-path draft transcripts)
- Health bar with distinct indicators:
  - **Mic:** recording / paused / device lost
  - **System audio:** healthy / degraded (BlackHole routing broken) / off (not configured)
  - **Transcription:** active / idle / error
  - **Cold path:** idle / running / last run time / failed
  - **Disk:** usage in human-readable format, warning indicator if over threshold
- Pause indicator (prominent, when recording is paused)

#### Keybindings

| Key   | Action                                      |
|-------|---------------------------------------------|
| `p`   | Toggle pause/resume recording               |
| `n`   | Open **note panel** — add context or notes  |
| `q`   | Open **query panel** — ask a question       |
| `d`   | Show disk usage detail                      |
| `?`   | Show keybinding help                        |
| `Esc` | Close current panel / return to main view   |
| `C-c` | Quit TUI (daemon continues)                 |

#### Note Panel (`n`)

A text input that appears over the caption stream. Used for:
- Context annotations: "starting call with Justin", "calling USAA about car loan"
- Contemporaneous meeting notes: free-form text while listening
- Quick markers: short labels that become retrieval anchors

On submit, the note is saved as a marker with the current timestamp. The panel
closes and returns to the main view. Notes are associated with the current
position in the timeline and appear in cold-path summaries as user-provided
context.

#### Query Panel (`q`)

A text input for asking questions about past transcripts.

v1 queries resolve against time windows and user-provided markers. Name-based
retrieval (e.g., "the call with Justin") works when the user has added a
context marker containing that name. Without a marker, the same query would
need to be phrased as a time window ("the call around 2pm"). Examples:

- "what was discussed in the last hour?"
- "summarize the 2pm to 3pm window" (always works — pure time-window query)
- "summarize the call with Justin" (works when user added a marker like
  "starting call with Justin" — name resolves via marker text)
- "what did I agree to on the USAA call?" (works when user added a marker
  like "calling USAA about car loan")

On submit:
1. If the cold-path worker is already warm, the query is sent immediately.
2. If a scheduled cold-path run is in progress, the worker is preempted
   (checkpoints its batch work), then handles the query. The user sees a brief
   spinner during the transition, not an unbounded wait.
3. If no worker is running, the daemon spawns one. A spinner shows in the
   panel while the model loads.
4. The response appears in the panel below the query input.
5. The user can ask follow-up questions. The worker stays warm for 5 minutes
   after the last query.
6. `Esc` closes the panel.

### 8. Pause, Privacy, and Data Purge

- The `p` keybinding sends a pause command to the daemon via IPC.
- When paused:
  - Audio capture stops immediately.
  - The TUI shows a prominent PAUSED indicator.
  - The daemon writes paused state to `daemon.json`.
  - No chunks are created. The timeline has an explicit gap.
  - A row is written to the `pauses` table with the pause start time. When
    recording resumes, the row is updated with the end time.
- Pressing `p` again resumes recording.
- `scarecrow pause` and `scarecrow resume` CLI commands also work without TUI.
- **Auto-pause on screen lock:** The daemon monitors macOS screen lock state
  via `CGSessionCopyCurrentDictionary` or `NSDistributedNotificationCenter`
  (`com.apple.screenIsLocked` / `com.apple.screenIsUnlocked`). When the
  screen locks (including lid close), recording is automatically paused. When
  the screen unlocks, recording resumes. This prevents accidental capture
  while the user is away. Auto-pause is enabled by default and configurable
  via `auto_pause_on_lock` in the config.
- **Delete recent recordings:** `scarecrow delete-last <duration>` purges
  audio files and all retrieval artifacts from the last N minutes. The delete
  window matches any chunk whose time interval overlaps the requested range.
  For example, `scarecrow delete-last 5m` hard-deletes the last 5 minutes of
  chunk rows, draft transcripts, canonical transcripts covering that window,
  matching FTS entries, markers created in that window, summaries whose
  summary windows overlap the deleted range, and stored query
  responses/provenance whose resolved windows or provenance rows intersect the
  deleted material. Overlapping pause rows are trimmed or deleted so the
  deleted interval is not preserved in timeline metadata. `delete-last` does
  NOT use `audio_pruned` and does NOT leave tombstones for the deleted
  content. This is a safety valve for accidental capture of sensitive content.
  The delete is permanent and logged only as counts and time range, never as
  transcript, marker, or query text.

### 9. Context and Retrieval

- There are no discrete sessions. The continuous timeline is the primary
  organizational unit.
- **Time windows** are the primary retrieval axis ("this afternoon",
  "last hour", "2pm to 3pm").
- **User-provided markers** are the secondary retrieval axis. When the user
  types "starting call with Justin" via the note panel, the name "Justin"
  becomes searchable. The query engine matches marker text against query terms
  to locate relevant time windows.
- **Marker window boundaries:** A marker defines the start of a window. The
  end of the window is the earliest of:
  1. The next marker's timestamp (a new marker implies a context change).
  2. The start of the next pause (user stepped away).
  3. A configurable maximum window duration (`marker_window_max_mins`,
     default 60 minutes).
  4. The current time (if the window is still open).
  If the query engine has access to transcript content (canonical or draft),
  it may also use an extended silence gap (>5 minutes of no speech on either
  channel) as a natural boundary hint, but the hard boundaries above always
  take precedence.
- **FTS5 full-text search** over merged canonical transcripts provides
  keyword-based retrieval ("when did someone mention the budget?"). Only
  `merged` channel transcripts with `is_current = TRUE` are indexed (see
  Search Surface rules in State and Provenance).
- **Calendar events** are a future enhancement (not in v1) that could
  correlate meeting times with transcript windows.
- The data model leaves room for future voiceprint enrollment to associate
  diarized speaker labels with names.
- v1 does NOT support automatic person-name speaker identification. Diarized
  speakers are labeled as "Speaker A", "Speaker B", etc.

## Audio Input Handling

### Dual-Channel Capture

Scarecrow captures two audio sources simultaneously into a single stereo chunk:

- **Channel 1 (mic):** The user's microphone. This is the primary input for
  hot-path captions and is always the user's own voice.
- **Channel 2 (system audio):** All system audio routed through BlackHole.
  This captures the remote side of calls, but also captures any other system
  audio (music, podcasts, notifications, etc.).

### Audio Sample Rate Pipeline

Audio devices and whisper.cpp operate at different sample rates. The pipeline
handles resampling explicitly:

1. **Capture:** CoreAudio captures at the device's native sample rate (typically
   48 kHz for both mic and BlackHole). The Multi-Output Device and all its
   sub-devices should use the same sample rate. The `scarecrow setup` wizard
   verifies that all devices in the Multi-Output Device match.
2. **Storage:** Opus chunks are encoded at the capture sample rate (48 kHz).
   Opus handles this natively and efficiently.
3. **Transcription (hot path):** Before feeding audio to whisper.cpp, the
   daemon resamples the mic channel from 48 kHz to 16 kHz in memory. This is
   a lightweight operation (~1 ms for a 30-second chunk on Apple Silicon).
4. **Transcription (cold path):** The Python worker resamples both channels
   to 16 kHz before inference. Libraries like `librosa` or `torchaudio` handle
   this.

The setup wizard does NOT ask users to set devices to 16 kHz. That would
degrade audio quality for other applications and is unnecessary — resampling
at the transcription layer is the correct approach.

#### BlackHole Setup (one-time)

BlackHole is a free, open-source virtual audio device for macOS. Setup:
1. Install BlackHole (2ch) via Homebrew: `brew install blackhole-2ch`
2. Open Audio MIDI Setup.
3. Create a Multi-Output Device: built-in output + BlackHole 2ch. This sends
   system audio to both speakers/headphones AND BlackHole.
4. Set the Multi-Output Device as the system output.
5. Scarecrow captures from both the mic and BlackHole as separate channels.

The daemon documents this setup in `scarecrow setup` which walks the user
through configuration.

**Known limitations of Multi-Output Devices:**
- The Multi-Output Device uses the first (top) device in the list as the clock
  source. BlackHole should NOT be the clock source — the physical output device
  should be listed first.
- Sample rate must match across all devices in the Multi-Output Device.
  The setup wizard verifies this (typically 48 kHz).
- If the physical output device is removed (e.g., Bluetooth disconnects), the
  Multi-Output Device may become invalid. Scarecrow detects this and falls back
  to mic-only mode until the output device returns.
- **Device switching is the primary operational risk.** When AirPods or
  Bluetooth headsets auto-connect, macOS switches the system output away from
  the Multi-Output Device entirely, silently breaking BlackHole routing. The
  daemon detects this via CoreAudio callbacks and falls back to mic-only mode
  with a warning in the TUI and logs. The user must manually switch back to
  the Multi-Output Device (or use a helper like SwitchAudioSource).
- Drift correction should be enabled on BlackHole, NOT on the physical output
  device (which serves as clock source).
- Volume control through the Multi-Output Device may not work for all sub-devices.
  Users may need to control volume on the physical device directly.

### Device Selection

- The config file specifies the mic device and the system audio device (BlackHole)
  separately by name or UID.
- If BlackHole is not configured or unavailable, the daemon falls back to
  mic-only mode and logs a warning. Scarecrow still functions — it just captures
  only the user's voice.
- If the mic device is unavailable at startup, the daemon falls back to the
  system default input and logs a warning.

### Device Changes and Channel Health

Device changes affect mic recording and system audio capture differently:

**Mic (channel 1) — input device:**
- If a Bluetooth headset is connected that changes the default input device,
  the daemon detects this via CoreAudio device-change callbacks.
- Behavior: the daemon logs the change and continues recording from the
  originally configured device. It does not auto-switch unless explicitly
  configured to follow the system default.
- If the configured device is physically removed (e.g., USB mic unplugged),
  the daemon pauses recording, logs an error, and resumes when the device
  reappears or falls back to system default (configurable).
- **Output device changes (headphones, Bluetooth speakers) do not affect mic
  recording.** The mic is an input device, independent of output routing.

**System audio (channel 2) — BlackHole via Multi-Output Device:**
- Output device changes CAN affect system audio capture. If macOS switches
  the system output away from the Multi-Output Device (e.g., AirPods
  auto-connect), BlackHole stops receiving system audio and channel 2 goes
  silent.
- The daemon detects this by monitoring the system default output device via
  CoreAudio callbacks. If the output is no longer the configured Multi-Output
  Device, the daemon:
  1. Logs a warning: "System output changed to [device]. BlackHole routing
     broken. System audio capture degraded to silent."
  2. Sets `sys_channel_healthy = false` in `daemon.json`.
  3. Continues recording mic-only (channel 2 captures silence).
  4. The TUI health bar shows degraded system audio status.
- When the user switches back to the Multi-Output Device, the daemon detects
  the change, restores `sys_channel_healthy = true`, and logs recovery.

## Data Model (SQLite)

All persistent data lives in a single SQLite database at
`$SCARECROW_DATA/scarecrow.db`. The database uses WAL mode for crash-safe
writes and concurrent read access from the TUI.

### chunks

| Column              | Type    | Description                              |
|---------------------|---------|------------------------------------------|
| id                  | INTEGER | Primary key                              |
| started_at          | TEXT    | ISO 8601 timestamp, chunk start          |
| ended_at            | TEXT    | ISO 8601 timestamp, chunk end            |
| duration_ms         | INTEGER | Actual duration in milliseconds          |
| audio_path          | TEXT    | Relative path to Opus file, NULL if silent |
| has_speech          | BOOLEAN | TRUE if VAD detected speech on either channel |
| mic_has_speech      | BOOLEAN | TRUE if VAD detected speech on mic channel |
| sys_has_speech      | BOOLEAN | TRUE if VAD detected speech on system channel |
| input_device        | TEXT    | Mic device UID used for this chunk       |
| sys_channel_healthy | BOOLEAN | TRUE if BlackHole routing was active during this chunk |
| transcription_state | TEXT    | 'pending', 'draft', 'canonical'          |
| audio_pruned        | BOOLEAN | TRUE if audio file has been deleted by retention sweep |

`transcription_state` tracks how far the transcription pipeline has progressed:
- `pending`: chunk recorded, no transcript yet (e.g., system-only speech
  awaiting cold path)
- `draft`: hot-path draft transcript exists
- `canonical`: cold-path canonical transcript exists (supersedes draft)

`audio_pruned` is orthogonal to `transcription_state`. A chunk can be
`canonical` with `audio_pruned = TRUE` — the audio is gone but the transcript
and summary remain.

`delete-last` is separate from retention sweeps. It hard-deletes overlapping
chunk rows and associated records rather than setting `audio_pruned = TRUE`.

`sys_channel_healthy` records the BlackHole routing state at the time of
capture. This distinguishes "channel 2 was silent because nobody was talking"
(`sys_has_speech = FALSE`, `sys_channel_healthy = TRUE`) from "channel 2 was
silent because BlackHole routing was broken" (`sys_has_speech = FALSE`,
`sys_channel_healthy = FALSE`). The cold path uses this to avoid treating
routing-broken silence as meaningful "no remote speaker" evidence.

### transcripts

| Column          | Type    | Description                              |
|-----------------|---------|------------------------------------------|
| id              | INTEGER | Primary key                              |
| chunk_id        | INTEGER | FK to chunks                             |
| tier            | TEXT    | 'draft' (hot path) or 'canonical' (cold path) |
| channel         | TEXT    | 'mic', 'system', or 'merged'             |
| text            | TEXT    | Full transcript text                     |
| confidence      | REAL    | Average confidence score (0.0-1.0)       |
| is_current      | BOOLEAN | TRUE if this is the active transcript for the chunk+channel |
| speaker_labels  | TEXT    | JSON array of {speaker, start, end, text} segments (canonical only) |
| engine_version  | TEXT    | Model name and version used              |
| created_at      | TEXT    | ISO 8601 timestamp                       |

See State and Provenance for indexing and supersession rules.

### summaries

| Column          | Type    | Description                              |
|-----------------|---------|------------------------------------------|
| id              | INTEGER | Primary key                              |
| window_start    | TEXT    | ISO 8601 timestamp, window start         |
| window_end      | TEXT    | ISO 8601 timestamp, window end           |
| text            | TEXT    | Summary text                             |
| model_version   | TEXT    | LLM model used                           |
| created_at      | TEXT    | ISO 8601 timestamp                       |

Summaries whose `[window_start, window_end]` range overlaps a `delete-last`
window are deleted rather than redacted so deleted content cannot survive on a
summary surface.

### markers

| Column          | Type    | Description                              |
|-----------------|---------|------------------------------------------|
| id              | INTEGER | Primary key                              |
| timestamp       | TEXT    | ISO 8601 timestamp the marker refers to  |
| label           | TEXT    | User-provided label or note text         |
| marker_type     | TEXT    | 'note' (all user-entered markers)        |
| created_at      | TEXT    | ISO 8601 timestamp                       |

Markers whose timestamps fall inside a `delete-last` window are deleted and
removed from `markers_fts`.

### pauses

| Column          | Type    | Description                              |
|-----------------|---------|------------------------------------------|
| id              | INTEGER | Primary key                              |
| started_at      | TEXT    | ISO 8601 timestamp, pause start          |
| ended_at        | TEXT    | ISO 8601 timestamp, pause end (NULL if still paused) |

### cold_path_runs

| Column            | Type    | Description                              |
|-------------------|---------|------------------------------------------|
| id                | INTEGER | Primary key                              |
| started_at        | TEXT    | ISO 8601 timestamp, run start            |
| completed_at      | TEXT    | ISO 8601 timestamp, run end (NULL if in progress) |
| processed_through | TEXT    | ISO 8601 timestamp of latest chunk fully processed |
| chunks_processed  | INTEGER | Number of chunks processed in this run   |
| status            | TEXT    | 'running', 'completed', 'interrupted', 'failed' |
| error_message     | TEXT    | Error details if status is 'failed'      |

- The next cold-path run picks up chunks with `started_at >` the most recent
  completed or interrupted run's `processed_through` value.
- If no completed/interrupted run exists, the worker processes all unprocessed
  chunks.
- `interrupted` means the run was preempted by a user query and checkpointed
  successfully. It is treated the same as `completed` for watermark purposes.

### queries

| Column          | Type    | Description                              |
|-----------------|---------|------------------------------------------|
| id              | INTEGER | Primary key                              |
| question        | TEXT    | User's question text                     |
| response        | TEXT    | Generated response                       |
| model_version   | TEXT    | LLM model used                           |
| window_start    | TEXT    | ISO 8601 timestamp, resolved query window start |
| window_end      | TEXT    | ISO 8601 timestamp, resolved query window end |
| marker_ids      | TEXT    | JSON array of marker IDs used to resolve the query |
| transcript_ids  | TEXT    | JSON array of transcript IDs fed to the LLM |
| transcript_tiers| TEXT    | JSON summary of tier mix, e.g., {"draft": 3, "canonical": 12} |
| created_at      | TEXT    | ISO 8601 timestamp                       |

The provenance columns (`window_start`, `window_end`, `marker_ids`,
`transcript_ids`, `transcript_tiers`) make it possible to debug wrong answers
after the fact: what time window was resolved, which markers matched, which
transcript rows were fed to the LLM, and whether those transcripts were
draft or canonical at query time.

Queries whose resolved windows overlap a `delete-last` window, or whose
provenance references transcripts or markers removed by `delete-last`, are
deleted rather than redacted.

### Future: calendar_events

Calendar integration is deferred beyond v1. When designed, it will introduce
its own schema informed by the actual calendar API, sync mechanism, and query
engine join strategy. No `calendar_events` table exists in the v1 schema.

## State and Provenance

This section defines how authoritative state is tracked and resolved for
objects that have multiple versions or lifecycle stages.

### Transcript Supersession

A chunk can have multiple transcript rows across tiers and channels.
The `is_current` flag determines which transcript is authoritative for each
chunk+channel combination:

1. When the hot path writes a draft mic transcript, `is_current` is set to
   TRUE for that chunk+channel ('mic').
2. When the cold path runs, it produces three transcript rows per chunk:
   - `channel='mic'`, `tier='canonical'` — re-transcription of mic audio
   - `channel='system'`, `tier='canonical'` — transcription of system audio
   - `channel='merged'`, `tier='canonical'` — combined timeline with speaker
     labels from diarization
3. On write, the cold path performs the following in a single transaction:
   - Sets `is_current = TRUE` on all three canonical rows.
   - Sets `is_current = FALSE` on the draft mic row.
   - Deletes the draft row from the FTS5 `transcripts_fts` index.
   - Inserts the merged canonical row into the FTS5 `transcripts_fts` index.
   - Updates the chunk's `transcription_state` to `'canonical'`.
4. Old draft rows are retained for debugging/audit but are invisible to the
   user and excluded from FTS5.

### Search Surface

The FTS5 index and all user-facing retrieval queries use a single search
surface: **`merged` channel transcripts with `is_current = TRUE`.**

- Before the cold path runs, a chunk has only a `draft` mic transcript. This
  is `is_current = TRUE` with `channel = 'mic'` and IS included in FTS5 so
  that recent speech is searchable immediately.
- After the cold path runs, the `merged` canonical transcript replaces the
  draft in the FTS5 index. Per-channel canonical transcripts (`mic`, `system`)
  are stored for internal use (debugging, confidence analysis) but are NOT
  indexed in FTS5.
- **Rule:** FTS5 indexes all transcripts where `is_current = TRUE` AND
  (`channel = 'merged'` OR (`channel = 'mic'` AND `tier = 'draft'`)). This
  ensures exactly one searchable transcript per chunk at any point in time.

FTS5 virtual table on `markers.label` for name/keyword search in queries.

### Cold-Path Run Tracking

The `cold_path_runs` table provides an explicit log of worker executions:

- Before processing, the worker inserts a row with `status = 'running'`.
- On success, it updates `status = 'completed'`, sets `processed_through` to
  the `ended_at` of the latest chunk it processed, and records `completed_at`.
- On preemption by a user query, it updates `status = 'interrupted'` with
  `processed_through` set to the last fully-processed chunk. The interrupted
  run's watermark is valid for the next run to resume from.
- On failure, it updates `status = 'failed'` with an error message.
- The daemon uses the latest `completed` or `interrupted` row's
  `processed_through` as the watermark for the next scheduled run.
- If the most recent run is `failed` or `running` (stale), the daemon logs a
  warning and re-processes from the last successful/interrupted watermark.

### Chunk Lifecycle

A chunk's lifecycle involves two orthogonal state dimensions:

1. **Transcription progress** (`transcription_state`): pending → draft →
   canonical. Moves forward only, never backward.
2. **Audio retention** (`audio_pruned`): FALSE → TRUE when the retention sweep
   deletes the audio file. Irreversible.

These are independent for retention sweeps only. A chunk in state
`canonical` + `audio_pruned = TRUE` means: the audio file has been deleted by
retention policy, but the canonical transcript and any summaries that reference
it remain searchable and usable. This does not apply to `delete-last`, which is
an explicit privacy purge and hard-deletes affected rows plus all retrieval
artifacts for the deleted window.

### Pause Intervals

Pauses are stored as intervals in the `pauses` table, not as point-in-time
markers:

- When pause is triggered, a row is inserted with `started_at` set and
  `ended_at` NULL.
- When resume is triggered, the open row's `ended_at` is set.
- If the daemon exits while paused, crash recovery sets `ended_at` to the
  daemon's exit time on next startup.
- The query engine and cold-path summaries use `pauses` to account for timeline
  gaps.

### Query Provenance

Each query response is stored with the model version, resolved window, matched
markers, and transcript IDs that generated it (see `queries` table schema).

Queries are answered against the current state of transcripts at query time.
This means the query engine may see a **mixed draft/canonical state**: recent
chunks may still have only draft mic transcripts, while older chunks have
canonical merged transcripts. This is expected and acceptable — the query
engine uses whatever `is_current = TRUE` transcripts exist. The
`transcript_tiers` column records the mix (e.g., `{"draft": 3, "canonical": 12}`)
so the user or a later review can see how much of the answer came from
draft vs. canonical data. The query does NOT trigger canonicalization of the
requested window — it answers from what exists now.

## Configuration

Config file: `$SCARECROW_CONFIG/scarecrow.toml` (default: `~/.config/scarecrow/scarecrow.toml`)

```toml
[audio]
mic_device = "default"             # Mic device name, UID, or "default"
system_device = ""                 # Empty = mic-only until BlackHole is configured
multi_output_device = ""           # Multi-Output Device name (for health monitoring)
chunk_duration_secs = 30
follow_system_default = false      # If true, auto-switch mic on device change
auto_pause_on_lock = true          # Auto-pause when screen locks / lid closes

[transcription]
hot_model = "tiny"                 # whisper.cpp model for live captions
cold_model = "large-v3"            # whisper.cpp model for canonical transcripts
cold_interval_mins = 30            # How often the cold path runs
confidence_threshold = 0.4         # Minimum confidence to keep system audio transcript

[worker]
enable_summaries = true            # Set false to skip LLM summaries (saves ~5 GB RAM)
enable_diarization = true          # Set false to skip pyannote diarization (saves ~300 MB RAM)

[llm]
model_dirs = ["~/Models", "~/.cache/llama.cpp"]
cleanup_model = ""                 # Preferred GGUF for cleanup; empty = auto-discover
summary_model = ""                 # Preferred GGUF for summaries; empty = auto-discover
query_model = ""                   # Preferred GGUF for queries; empty = auto-discover
backend = "llama-server"           # "llama-server" or "llama-cli"
catalog_path = "~/.local/share/scarecrow/state/model_catalog.json"

[query]
idle_timeout_secs = 300            # How long the worker stays warm after last query
marker_window_max_mins = 60        # Max duration of a marker-resolved window

[storage]
data_dir = "~/.local/share/scarecrow"
audio_retention_days = 14
disk_warning_gb = 10               # Warn when total usage exceeds this

[logging]
level = "info"                     # trace, debug, info, warn, error
max_file_size_mb = 50
max_files = 5
```

Leaving `system_device` empty is the default clean-machine state. The setup
wizard fills this in only when BlackHole capture is configured.

Data directory structure:
```
$SCARECROW_DATA/
├── scarecrow.db          # SQLite database
├── audio/                # Opus chunk files, organized by date
│   └── 2026-03-14/
│       ├── 0930-001.opus
│       └── 0930-002.opus
├── models/               # Downloaded whisper/LLM models
├── state/
│   ├── daemon.json       # Daemon health and status
│   ├── daemon.pid        # PID file
│   ├── scarecrow.sock    # Daemon<->TUI Unix socket
│   └── worker.sock       # Daemon<->Worker Unix socket (when worker is in query mode)
└── logs/
    └── scarecrow.log     # Rotated structured logs
```

## Language Assignments

| Component        | Language | Rationale                                        |
|------------------|----------|--------------------------------------------------|
| scarecrow-daemon | Rust     | Low memory, no GC, direct FFI to whisper.cpp, CoreAudio bindings |
| scarecrow (TUI)  | Rust     | Shares types/IPC protocol with daemon, single toolchain |
| scarecrow-worker | Python   | pyannote-audio plus `llama.cpp` orchestration for cleanup, summaries, and query answering |
| Build system     | Cargo    | Workspace with daemon and TUI as separate crates  |

The Rust workspace contains two binary crates (`scarecrow-daemon`, `scarecrow`)
and shared library crates for types, IPC protocol, and database access.

The Python worker is a standalone script invoked by the daemon as a subprocess.
Its dependencies are managed in a dedicated virtualenv at
`$SCARECROW_DATA/venv/`.

### Local Model Discovery and Selection

Scarecrow uses a config-first selection policy for local GGUF models.

Selection order:
1. Explicit model names/paths from `[llm]` in `scarecrow.toml`
2. Auto-discovery from configured `model_dirs`
3. Feature disablement with a clear error if no compatible model is available

An empty `cleanup_model`, `summary_model`, or `query_model` value is an
intentional request to use auto-discovery for that role.

For each discovered model, the worker should track a local catalog with:
- file path
- file size
- quantization
- last modified time
- intended use (`cleanup`, `summary`, `query`)
- validation status (`untested`, `ok`, `failed`)

The catalog is stored at `$SCARECROW_DATA/state/model_catalog.json`.

Fallback heuristics:
- prefer smaller instruct models for transcript cleanup
- prefer larger instruct models with more context for summaries and queries
- skip candidates that exceed the current memory budget
- smoke-test a model on first use before marking it healthy
- if multiple healthy candidates exist for a role, choose deterministically by
  intended-use match, then context suitability, then lower resource cost

Shell aliases such as `cclocal` are explicitly out of scope for runtime
integration. They are developer conveniences only, not product dependencies.

### Staged TUI Delivery

The full v1 TUI main view includes mic, system audio, transcription, cold-path,
and disk indicators. Implementation is staged:

- M5 delivers the baseline view: captions plus mic/system/transcription health
- M7 adds live cold-path state
- M9 adds live disk state and warnings

This is a delivery sequence only; the full v1 contract remains the five-part
health bar described in the main view section.

## Diarization Engine

The cold-path worker needs speaker diarization to label "Speaker A",
"Speaker B", etc. in canonical transcripts.

### Primary: pyannote-audio

- Model: `pyannote/speaker-diarization-3.1` (or community equivalent)
- **Access requirement:** pyannote models are gated on HuggingFace. The user
  must accept the model license terms on huggingface.co (both
  `pyannote/segmentation-3.0` and `pyannote/speaker-diarization-3.1`) and
  provide a HuggingFace API token. The `scarecrow setup` wizard handles this.
- License: MIT for the library; model weights require license acceptance
  (free for research/personal use).
- The HuggingFace token is stored in the standard `~/.cache/huggingface/token`
  location (see Security and File Permissions section).

### Fallback: no diarization

- If pyannote is not configured or the token is missing, the cold path runs
  without diarization. Canonical transcripts are produced without speaker
  labels. This is a degraded but functional state.
- The setup wizard clearly explains what diarization provides and what is
  lost without it.

### Future: alternative engines

- SpeechBrain (Apache 2.0, ungated models) is the strongest alternative if
  pyannote's gated access becomes a friction problem.
- The worker's diarization interface should be a pluggable function, not
  hard-wired to pyannote.

## Storage and Retention

- **Audio (Opus):** Automatically pruned after 14 days. The daemon runs a
  retention sweep daily, deletes audio files, and sets `audio_pruned = TRUE`
  on the corresponding chunk rows.
- **Transcripts:** Retained indefinitely. Draft transcripts are superseded when
  canonical versions are produced (see State and Provenance).
- **Summaries:** Retained indefinitely.
- **Markers:** Retained indefinitely.
- **Queries:** Retained indefinitely.
- **Cold-path run log:** Retained indefinitely (small rows, useful for
  debugging).
- **Disk usage warning:** The daemon monitors total data directory size. When
  it exceeds 10 GB (configurable), a warning is surfaced in the TUI and logged.
  Disk usage is also shown when the TUI connects and disconnects.
- **Model files:** Not counted toward the disk warning threshold. Managed
  separately.

## Error and Logging Strategy

- Structured JSON logging via `tracing` (Rust) / `structlog` (Python worker).
- Log rotation: max 50 MB per file, 5 files retained (configurable).
- Log levels: trace, debug, info, warn, error.
- Key events logged at `info`: daemon start/stop, chunk recorded, transcription
  complete, cold-path run start/finish, device change, retention sweep,
  pause/resume, worker spawn/exit.
- Key events logged at `warn`: device fallback, disk threshold exceeded,
  transcription failure on retry, BlackHole unavailable (mic-only fallback),
  Multi-Output Device invalid, system output device changed (ch2 degraded),
  cold-path run failed or interrupted (will retry/resume).
- Key events logged at `error`: mic device lost, daemon crash recovery,
  database corruption detected.

## Security and File Permissions

### Threat Model

Scarecrow assumes a **single-user macOS system** where the primary threat is
accidental exposure of sensitive conversation data through file permissions,
cloud backup sync, or device theft. It does NOT attempt to defend against a
malicious actor with root access or physical access to an unlocked machine.

### File Permissions

All files and directories created by Scarecrow use restrictive permissions:

| Path                          | Permission | Rationale                          |
|-------------------------------|------------|------------------------------------|
| `$SCARECROW_DATA/`           | 0700       | Contains all sensitive data        |
| `$SCARECROW_DATA/scarecrow.db` | 0600     | Full conversation transcripts      |
| `$SCARECROW_DATA/audio/`     | 0700       | Raw voice recordings               |
| `$SCARECROW_DATA/audio/**`   | 0600       | Individual audio files             |
| `$SCARECROW_DATA/state/`     | 0700       | PID, sockets, status               |
| `$SCARECROW_DATA/state/*.sock` | 0600     | IPC sockets (prevents eavesdrop)   |
| `$SCARECROW_DATA/logs/`      | 0700       | May contain metadata               |
| `$SCARECROW_DATA/logs/*`     | 0600       | Log files                          |
| `$SCARECROW_CONFIG/scarecrow.toml` | 0600 | May contain HF token path          |

### Log Content Policy

Log entries MUST NOT contain:
- Full or partial transcript text
- Marker label content
- Query question or response text
- File paths to specific audio chunks (use relative paths or chunk IDs)

Log entries MAY contain:
- Chunk IDs, counts, and timestamps
- Device names and UIDs
- Model names and versions
- Operational metrics (latency, RSS, disk usage)

### Cloud Backup Exclusion

The `$SCARECROW_DATA/` directory should be excluded from cloud backup services
(iCloud, Time Machine, Dropbox, etc.). The `scarecrow setup` wizard warns
about this and offers to set the macOS `com.apple.metadata:com_apple_backup_excludeItem`
extended attribute on the data directory.

### IPC Message Validation

All IPC channels (daemon<->TUI, daemon<->worker) enforce:
- Maximum message size: 1 MB per message
- Strict schema validation via `serde` deserialization (Rust) or JSON schema
  (Python). Malformed messages are logged and dropped, never crash the daemon.
- Marker text and query text are treated as untrusted input when used in LLM
  prompts. The worker sanitizes these inputs (strip control characters,
  truncate to reasonable length) before prompt construction.

### Model Integrity Verification

Downloaded model files are verified against pinned SHA256 checksums before
loading. Checksums are maintained in the Scarecrow source code (or config)
for each supported model version. If a checksum does not match, the model
is rejected, deleted, and the download is retried. This protects against
corrupted downloads and supply-chain tampering.

### HuggingFace Token Storage

The HuggingFace API token is stored ONLY in the standard HuggingFace cache
location (`~/.cache/huggingface/token`), NOT in `scarecrow.toml`. This
avoids duplicating credentials and follows HuggingFace's own security
practices. The setup wizard configures the token via `huggingface-cli login`.

## First Run and Setup

### macOS Permissions

- The daemon requires microphone access. On first run, macOS will prompt for
  permission. If denied, the daemon exits with a clear error message and
  instructions to grant access in System Settings > Privacy & Security > Microphone.
- Screen recording permission is NOT required (BlackHole captures audio without it).

### Model Download

- On first run, the daemon checks for the hot-path model (`tiny`) in
  `$SCARECROW_DATA/models/`. If missing, it downloads it automatically from
  the whisper.cpp model repository and logs progress (~75 MB download).
- The cold-path model (`large-v3`) is downloaded on first cold-path run, not
  at daemon startup. This avoids a 3 GB download blocking the first launch.
- All model downloads are verified against pinned SHA256 checksums before
  loading (see Security and File Permissions section).
- `scarecrow setup` includes a step to pre-download models if desired.

### BlackHole Setup

- `scarecrow setup` walks the user through BlackHole installation and
  Multi-Output Device creation with step-by-step instructions, including
  clock source ordering, drift correction, and sample rate verification
  (all devices should match, typically 48 kHz).
- If BlackHole is not configured, the daemon operates in mic-only mode.
  This is a degraded but functional state — the user gets their own voice
  transcribed but not remote call participants.
- Skipping BlackHole leaves `system_device = ""` and `multi_output_device = ""`
  in the config rather than writing a placeholder device name.

### Diarization Setup

- `scarecrow setup` explains the pyannote HuggingFace token requirement,
  including that license terms must be accepted on two model pages.
- The user can skip this step. Without it, canonical transcripts have no
  speaker labels.
- The setup wizard stores the token in the standard HuggingFace cache
  location.

## Control Plane: daemon.json

The daemon writes a JSON status file at `$SCARECROW_DATA/state/daemon.json`.
This file is the primary interface for `scarecrow status` and for crash
recovery timestamp resolution.

### Schema

```json
{
  "pid": 12345,
  "started_at": "2026-03-14T09:00:00Z",
  "updated_at": "2026-03-14T14:32:10Z",
  "state": "recording",
  "paused": false,
  "mic_device": "Built-in Microphone",
  "sys_channel_healthy": true,
  "sys_device": "BlackHole 2ch",
  "hot_model": "tiny",
  "chunks_recorded": 1847,
  "last_chunk_at": "2026-03-14T14:32:00Z",
  "cold_path_last_run": "2026-03-14T14:00:00Z",
  "cold_path_status": "completed",
  "worker_pid": null,
  "worker_mode": null,              // null, "cold-path", or "query"
  "disk_usage_bytes": 524288000,
  "disk_warning": false
}
```

### Write Rules

- **Write cadence:** Updated every 10 seconds and on every state change
  (pause, resume, device change, worker spawn/exit, cold-path status change).
- **Atomic write:** The daemon writes to a temporary file
  (`daemon.json.tmp`) and renames it to `daemon.json`. This prevents
  `scarecrow status` from reading a partial file.
- **`updated_at`** is always set to the current time on every write. Crash
  recovery uses this as the daemon's "last known alive" timestamp.

## Crash Recovery

- The daemon writes a PID file at `$SCARECROW_DATA/state/daemon.pid`.
- On startup, if a stale PID file exists (process not running), the daemon
  cleans it up and starts normally.
- On startup, the daemon unlinks stale Unix socket files (`scarecrow.sock`,
  `worker.sock`) if they exist from a previous run. Stale sockets prevent
  new listeners from binding.
- On startup, the daemon scans `$SCARECROW_DATA/audio/` for partial/orphaned
  chunk files (files without a corresponding completed database row). These
  are discarded and logged.
- On startup, if a `pauses` row has `ended_at = NULL`, the daemon sets
  `ended_at` to the daemon's last known alive time (from `daemon.json`) and
  logs the recovery.
- On startup, if a `cold_path_runs` row has `status = 'running'`, it is
  updated to `status = 'failed'` with an error message noting the daemon
  restart.
- The daemon registers signal handlers:
  - **SIGTERM / SIGINT:** Finalize the current in-flight chunk (flush audio to
    disk, write DB row), then exit cleanly. Maximum grace period: 5 seconds.
  - **SIGQUIT:** Immediate exit (for emergencies). Current chunk is lost.
- The SQLite database uses WAL mode for crash-safe writes.

## Implementation Notes

### SQLite Configuration

All database connections (daemon, TUI, worker) MUST set these pragmas:
```sql
PRAGMA journal_mode = WAL;
PRAGMA busy_timeout = 5000;
PRAGMA wal_autocheckpoint = 1000;
PRAGMA foreign_keys = ON;
```
`busy_timeout` is essential: the daemon and worker are separate processes that
both write to the database. WAL allows only one writer at a time. Without
`busy_timeout`, the second writer receives `SQLITE_BUSY` immediately.

### IPC Serialization

All Unix socket communication uses **JSON with 4-byte big-endian length-prefix
framing**: a 4-byte length header followed by a UTF-8 JSON payload. This
applies to both `scarecrow.sock` (daemon<->TUI) and `worker.sock`
(daemon<->worker). JSON is chosen for debuggability (`socat` inspection) and
because the message rate is low (~1/second for captions, occasional commands).

### Recommended Crates (Rust)

| Purpose          | Crate         | Notes                                    |
|------------------|---------------|------------------------------------------|
| whisper.cpp FFI  | `whisper-rs`  | Pin a specific `whisper-rs-sys` version   |
| Silero VAD       | `ort`         | ONNX Runtime bindings; handle LSTM state  |
| Audio I/O        | `cpal` + `coreaudio-rs` | `cpal` for capture, `coreaudio-rs` for device-change callbacks |
| Opus encoding    | `opusenc` CLI | Shell out to `opusenc` for v1; pure Rust Ogg+Opus is tedious |
| Resampling       | `rubato`      | 48kHz->16kHz is a simple 3:1 integer-ratio downsample |
| Ring buffer      | `ringbuf`     | Lock-free SPSC for CoreAudio callback -> consumer thread |
| SQLite           | `rusqlite`    | Worker uses Python's built-in `sqlite3`   |
| Serialization    | `serde_json`  | For IPC and daemon.json                   |
| Logging          | `tracing` + `tracing-subscriber` | JSON formatter with file rotation |
| TUI              | `ratatui`     | Terminal UI framework                     |

### Silero VAD Model

The `ort` crate provides ONNX Runtime bindings but does not bundle the Silero
VAD model. The model file (`silero_vad.onnx`) must be downloaded from the
[Silero VAD repository](https://github.com/snakers4/silero-vad) and placed in
`$SCARECROW_DATA/models/`. The daemon requires this file at startup alongside
the whisper `tiny` model and fails with a clear error if it is missing.
`scarecrow setup` downloads and verifies this file automatically. Before the
wizard exists, developers download it manually (see DEVELOPMENT.md). The model
is small (~2 MB) and does not require authentication.

### Opus Encoding Strategy

For v1, the daemon shells out to the `opusenc` command-line tool to encode
each chunk. This avoids the complexity of constructing OggOpus containers in
Rust. The process-spawn overhead is negligible for 30-second chunks. A
pure-Rust path can replace this later if needed.

### Cold-Path Scheduling

The daemon runs a timer that triggers the cold-path worker every
`cold_interval_mins` minutes. If `cold_interval_mins = 0`, the cold path is
disabled entirely (the daemon never spawns the worker for batch processing;
on-demand queries still work). The timer resets after each completed or
interrupted run, not from a fixed wall-clock schedule.

## Implementation Milestones

Each milestone has acceptance criteria. See `tasks.md` for detailed task
breakdown with measurable validation steps.

### M1: Foundation
- Rust workspace compiles. Config parsing works. SQLite schema created on
  first run. `scarecrow status` reads a mock `daemon.json`. Crash recovery,
  structured logging, model integrity, and microphone permission handling
  are implemented.
- **Done when:** `cargo build` succeeds, `scarecrow status` prints health
  from a status file, database is created with all tables on first run.
  Crash recovery cleans stale state on startup. Structured logging writes
  valid JSON lines with rotation. Model download verifies SHA256 checksums.
  Microphone permission denial produces a clear error. See `tasks.md`
  M1.1 through M1.9 for sub-milestone detail.

### M2: Audio Capture
- Daemon captures dual-channel audio from mic + BlackHole. Chunks written as
  Opus files. Falls back to mic-only if BlackHole is absent. System audio
  channel health tracked.
- **Done when:** 30-second Opus files appear in `audio/` directory. Stereo
  when BlackHole is present, mono when not. Cross-fade test shows <10 ms gap
  between chunks. System output device change triggers ch2 degraded warning.

### M3: Hot-Path Transcription
- whisper.cpp FFI integrated. `tiny` model transcribes mic channel. Draft
  transcripts written to SQLite.
- **Done when:** Draft transcript rows appear in DB within 2 seconds of chunk
  completion. WER (word error rate) on a reference recording is <25% for
  clear English speech. Daemon RSS stays under 200 MB after 10+ chunks.

### M4: VAD and Silence Filtering
- Silero VAD runs on both channels. Silent chunks are discarded. Channel-aware
  routing table implemented.
- **Done when:** Silent periods produce no audio files. System-only speech
  chunks are retained but produce no draft transcript. VAD adds <100 ms
  processing time per chunk.

### M5: IPC and TUI Basics
- Unix socket IPC between daemon and TUI. TUI displays live captions and
  health status. `scarecrow start/stop` lifecycle works.
- **Done when:** TUI connects, shows scrolling captions, and can be
  detached/reattached without affecting recording. Health bar shows channel
  health for both mic and system audio.

### M6: TUI Panels and Pause
- Note panel, query panel (without LLM backend), pause/resume, disk and
  help overlays, and `delete-last` privacy purge. Markers persisted to
  SQLite. Implementation is split across phases: M6.1–M6.4 land in P3,
  M6.5 (`delete-last`) lands in P6 after the query engine exists.
- **Done when:** `n` opens note panel, markers appear in DB. `p` pauses
  recording with timeline gap. Query panel shell exists and can show a
  placeholder before backend integration. `d` and `?` overlays work.
  `scarecrow delete-last` hard-deletes chunks, transcripts, FTS entries,
  markers, summaries, and query rows for the specified window.

### M7: Cold-Path Worker
- Python worker runs on schedule. Re-transcribes with `large-v3`. Diarization
  with pyannote (or graceful fallback). Transcript cleanup and summaries via
  direct `llama.cpp`. Query preemption works.
- **Done when:** Canonical merged transcripts appear in DB after cold-path run.
  `is_current` flags update correctly. FTS5 returns merged text, not
  per-channel. Summaries cover the processed window. A query sent during a
  scheduled run preempts the run within 30 seconds.

### M8: Query Engine
- Worker answers user queries from query panel. Stays warm for follow-ups.
  Marker text used for name-based retrieval.
- **Done when:** User can ask "what was discussed in the last hour?" and get
  a response that references actual transcript content. Marker-based name
  queries resolve to the correct time window. Follow-up query reuses warm
  worker (no model reload visible in logs).

### M9: Retention and Disk Management
- Audio pruned after 14 days. Disk usage displayed and warned at threshold.
  `audio_pruned` flag set correctly.
- **Done when:** Old audio files are deleted on schedule. Chunk rows have
  `audio_pruned = TRUE` but `transcription_state` unchanged. TUI shows disk
  usage. Warning appears when threshold exceeded.

### M10: Setup Wizard and First Run
- `scarecrow setup` handles BlackHole, model download, mic permission,
  HuggingFace token.
- **Done when:** A clean install can go from zero to recording by following
  the setup wizard. Each optional component can be skipped with a clear
  degraded-mode explanation. Skipping BlackHole leaves mic-only config values.
  Selected cleanup, summary, and query models are validated through direct
  `llama.cpp` invocation before being marked healthy.

### M11: Integration and Longevity
- Cross-milestone validation verifies end-to-end data flow, crash recovery,
  long-run stability, concurrent SQLite writers, disk-full behavior, and
  daemon restart continuity.
- **Done when:** Full pipeline integration passes. Crash during supersession
  does not leave stale FTS or partial canonical state. Soak, concurrent
  writer, disk-full, and restart-continuity checks all pass.
