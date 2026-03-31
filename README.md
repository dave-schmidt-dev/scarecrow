<p align="center">
  <img src="assets/scarecrow-icon.svg" width="128" alt="Scarecrow">
</p>

# Scarecrow

Always-recording TUI for transcription and inline notes.

Scarecrow uses parakeet-mlx for accurate batch transcription. You can attach timestamped notes inline by typing prefix commands (`/task`) or plain text. Audio and transcripts are saved per-session.

**Backend:** parakeet-mlx (Parakeet TDT 1.1B) on Apple Silicon GPU — VAD-based chunking (drains at 750ms speech pauses), ~0.010x RTF

## Bug Tracking

Bug entries live inline in [HISTORY.md](HISTORY.md) under their date heading as `### [BUG-...]` sections. Future fixes must follow these rules:

- Add a `### [BUG-]` entry under the current date in `HISTORY.md` whenever a bug is found, investigated, worked around, or fixed.
- A bug is not considered squashed until a regression test exists for the exact failing logic path and that test passes.
- Do not rely on heavily mocked alternate paths to declare a bug fixed.
- Record temporary mitigations as workarounds until the root cause is actually fixed.

## Requirements

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) (recommended)
- macOS with microphone access

## Setup

```bash
git clone <repo-url> && cd scarecrow
python3 scripts/setup.py   # checks prereqs, installs deps, shows launch options
```

> **Maintainer note:** `scripts/setup.py` is the first thing new users run. Keep it in sync with README.md and pyproject.toml — if you change requirements, launch methods, or architecture, update the setup script too.

**Which launch method to use:**
- **iTerm2 users (recommended):** Use the iTerm2 profile — it handles font sizing, auto-close, and calls the venv binary directly.
- **Everyone else:** Add a shell alias pointing to `.venv/bin/scarecrow` (see Shell alias section below).

### iTerm2 profile (recommended)

Scarecrow includes a dynamic iTerm2 profile (`Scarecrow`) with a larger font (Monaco 16pt) for readability. Copy the example profile to iTerm2's dynamic profiles directory:

```bash
cp examples/scarecrow-iterm-profile.json \
   ~/Library/Application\ Support/iTerm2/DynamicProfiles/scarecrow.json
```

Edit the file to update the path to your scarecrow project (replace `$HOME/path/to/scarecrow` with the actual path).

Then add this alias to `~/.zshrc`:

```bash
alias sc='open -a iTerm && osascript -e "tell application \"iTerm2\" to create window with profile \"Scarecrow\""'
```

This opens a dedicated iTerm2 window that auto-runs scarecrow, shows errors on failure, and closes 3 seconds after a clean exit.

### Shell alias (alternative)

If you don't use iTerm2, add to `~/.zshrc` (or `~/.bashrc`):

```bash
alias sc="/path/to/scarecrow/.venv/bin/scarecrow"
```


## Usage

```bash
sc                   # launch Scarecrow (auto-starts recording + sys audio)
sc --no-sys-audio    # launch without system audio capture
```

**Keybindings** inside the TUI:
- `Ctrl+P` — pause / resume (releases microphone while paused)
- `Ctrl+Shift+Q` — quick quit (skip summary, still saves transcript + audio)
- `Ctrl+Q` — quit (full cleanup + summary)
- `Ctrl+Shift+D` — discard session & quit (moves session to `.discarded/`)
- `Enter` — submit note (or, at startup, start recording)

**Commands** (type at the start of your note, then press Enter):
- `/task` or `/t` — submit note tagged `[TASK]`
- `/flush` or `/f` — force-flush the audio buffer (transcribe immediately)
- `/context` or `/c` — add background context (spelling, names — improves summary accuracy, not surfaced directly)
- no prefix — submit note tagged `[NOTE]` (default)

**Help:**
- `/help`, `/h`, or `?` — show available commands and keybindings inline in the transcript pane

### TUI layout

The TUI shows:
- **Info bar** — recording state (`REC` / `PAUSED`), mic indicator with level label (quiet/low/med/high), elapsed time, word count, buffer/batch countdown, and an audio level meter (▁▂▃▄▅▆▇█) with color coding (green/yellow/red) using a log scale with peak-hold decay
- **Transcript pane** — batch transcription output with timestamped dividers (scrollable); every session begins with a `Session Start: YYYY-MM-DD HH:MM:SS` line and ends with a `Session End: YYYY-MM-DD HH:MM:SS` line
- **Notes pane** — text input for inline annotations; `/task` or `/t` prefix tags as `[TASK]`; plain text defaults to `[NOTE]`; `/context` or `/c` provides background info for the summarizer; notes are written to the transcript pane and transcript file with a wall-clock timestamp
- **Footer** — keybindings

### Pause behavior

- Microphone is released (system mic indicator turns off)
- Buffered audio is transcribed immediately before pausing
- A "Recording paused" marker is written to the transcript pane and file
- Elapsed timer continues running (tracks total session time)
- On resume, batch countdown resets for clean intervals

### Shutdown output

Scarecrow has three quit modes:

| Shortcut | What happens |
|---|---|
| `Ctrl+Q` | Full quit — saves everything, compresses audio, generates summary |
| `Ctrl+Shift+Q` | Quick quit — saves transcript + audio, skips summary |
| `Ctrl+Shift+D` | Discard — moves session to `.discarded/` (confirmation required) |

On quit, Scarecrow prints session metrics (duration, word count, file sizes) to the terminal.

Shutdown runs in two phases for a responsive TUI:
- **Phase 1** (in-TUI, fast): stop microphone, flush final audio batch, close session files
- **Phase 2** (post-TUI, terminal output): compress WAV→FLAC, generate summary

Ctrl+C uses the same cleanup path as Ctrl+Q.

### Auto-summarization

When a session ends, Scarecrow generates `summary.md` in the session directory using a local LLM (Nemotron-3-Nano in-process). The summary includes:
- Prose summary of the transcript with `/note` entries woven in naturally
- `/task` entries listed as a Markdown checklist at the bottom
- Footer with model name, word counts, token usage, and summarization time

`/context` entries provide background information (names, spelling, domain terms) that improves summary quality without appearing in the output.

Summarization requires a Nemotron GGUF model in the HuggingFace cache and `llama-cpp-python` (installed automatically). The model is loaded in-process — no server needed.

If the model produces only reasoning with no structured output, the summarizer automatically retries with a forced prefix. If both attempts fail, `summary.md` contains error details and a retry command:
```bash
python3 scripts/resummarize.py ~/recordings/<session-dir>
```

### Startup output

On launch, Scarecrow prints:
- Which model is loading with timing
- Where recordings and transcripts are saved

The parakeet model is preloaded during the prepare phase before the TUI launches, so the first batch transcription fires immediately without a cold-load delay. Models run in offline mode (`HF_HUB_OFFLINE=1`) to avoid network stalls. Debug logs are written to `~/.cache/scarecrow/debug.log`.

### Architecture

Scarecrow uses parakeet-mlx as its sole transcription engine. A 16kHz audio stream is buffered and fed to the model using VAD-based chunking — audio drains at natural speech pauses (750ms+ silence) with a 30-second hard max for continuous speech, polled every 150ms. Audio capture, transcription, and the TUI all run in a single process.

**Hallucination prevention:** Before sending audio to Parakeet, the VAD checks the speech-frame ratio of drained audio — if fewer than 15% of chunks contain speech energy (`VAD_MIN_SPEECH_RATIO`), the buffer is silently dropped. After transcription, a post-inference filter catches repeated-word hallucinations (e.g., "the the the the").

Inline notes are typed in the notes pane and submitted with Enter. The tag is determined by an optional prefix at the start of the text: `/task` or `/t` for `[TASK]`, or no prefix for `[NOTE]`. Each note is written to the transcript pane and the transcript file with a wall-clock timestamp and tag prefix. Notes work in any app state (recording, paused, or idle).

Transcript dividers show the start time of each audio batch window (not the time the model finishes processing). Dividers appear at most every 60 seconds. Consecutive batch results are joined into flowing paragraphs between dividers.

**System audio capture (on by default):** Scarecrow captures system audio via BlackHole and transcribes both channels through Parakeet. On startup, the default output switches to "Scarecrow Output" (a Multi-Output Device routing audio to speakers + BlackHole); on exit, the original output is restored. Mic transcripts display normally; sys transcripts show with a dim `◁` prefix. An echo filter suppresses mic duplicates when not using headphones. Per-source mute: `Ctrl+M` (mic), `Ctrl+Shift+S` (sys). Requires one-time setup of "Scarecrow Output" in Audio MIDI Setup. Use `--no-sys-audio` to disable.

## Session files

Each recording session creates a timestamped directory:

```
recordings/
  2026-03-24_07-48-36/
    audio.flac           # mic recording, lossless FLAC (compressed from WAV on shutdown)
    audio_sys.flac       # system audio (BlackHole)
    transcript.jsonl     # JSON Lines transcript — one event per line
    summary.md           # LLM-generated session summary (auto-created on shutdown)
```

### Transcript format (JSON Lines)

Each line in `transcript.jsonl` is a JSON object with a `type` field:

```jsonl
{"type":"session_start","timestamp":"2026-03-28T14:30:00","session_dir":"/path/to/session"}
{"type":"transcript","elapsed":0,"text":"Transcribed speech appears here."}
{"type":"note","tag":"TASK","timestamp":"14:30:20","text":"follow up on X"}
{"type":"warning","timestamp":"14:30:25","text":"Audio input overflow"}
{"type":"divider","elapsed":60}
{"type":"pause","elapsed":75}
{"type":"session_end","timestamp":"2026-03-28T14:31:00"}
```

Event types: `session_start`, `session_end`, `transcript`, `divider`, `pause`, `note`, `warning`. This format is designed for automated processing (summarization, task extraction) — filter by type, parse fields, no regex needed.

### Audio format

Audio is recorded as WAV (raw PCM in the audio callback, zero CPU overhead). On shutdown, WAV is automatically compressed to lossless FLAC (~2:1 size reduction, ~0.9 MB/min) and the WAV is deleted.

## Development

```bash
uv sync --no-editable                # install deps
bash scripts/run_test_suite.sh       # run tests
uv run ruff check scarecrow/ tests/  # lint
uv run vulture scarecrow/ vulture_whitelist.py  # dead code check
```

**After editing source files**, rebuild before testing:
```bash
uv sync --reinstall-package scarecrow --no-editable
```
The non-editable install means the venv has a snapshot copy of the source — edits don't take effect until you rebuild.

**Do not run `pytest` directly** — always use `bash scripts/run_test_suite.sh`. The suite runner isolates each test file in its own subprocess to handle PortAudio teardown cleanly.

Pre-commit hooks run ruff (lint + format) and vulture automatically.

When fixing bugs, add a `### [BUG-]` entry under the current date in `HISTORY.md` and add or extend the matching regression test in the same change.

### Git hooks

Install the repo hooks after syncing the environment:

```bash
uv run pre-commit install --hook-type pre-commit --hook-type pre-push
```

The hooks enforce these repo rules:

- fail the commit if `README.md` or `HISTORY.md` is missing
- fail the commit if staged code changes do not include an update to `HISTORY.md`
- fail the commit if `HISTORY.md` contains a squashed `### [BUG-]` entry without a regression test reference
- run `ruff` and `vulture` on pre-commit
- run the full test suite on pre-push

## Troubleshooting

### Model download failures / offline mode

Scarecrow runs with `HF_HUB_OFFLINE=1` to avoid network stalls on launch. This means the model must already be cached locally. If you see a model-not-found error:

1. Run once without offline mode to download: `HF_HUB_OFFLINE=0 scarecrow` (or set the env var before launching).
2. Or download manually to `~/.cache/huggingface/hub/`.

### Microphone permission errors (macOS)

If the mic fails to open, grant Terminal or iTerm2 microphone access:

**System Settings > Privacy & Security > Microphone** — enable the terminal app you use.

### Debug logs

Detailed error output is written to `~/.cache/scarecrow/debug.log`. Check this file if Scarecrow exits unexpectedly or transcription stops producing output.

## Architecture

```
scarecrow/
  __main__.py        # entry point, model preloading, startup output
  app.py             # Textual TUI, batch transcription scheduling, notes pane
  config.py          # audio settings, parakeet model config, defaults
  recorder.py        # sounddevice audio capture + WAV writing
  runtime.py         # HF offline bootstrap, parakeet model manager
  session.py         # timestamped session dirs + transcript files
  summarizer.py      # LLM summarization via llama-cpp-python (in-process)
  transcriber.py     # VAD-based parakeet-mlx batch transcription
  app.tcss           # TUI stylesheet
assets/
  scarecrow-icon.svg # app icon
scripts/
  setup.py           # bootstrap: checks prereqs, installs deps, shows config + launch options
  resummarize.py     # re-run summarization on an existing session
examples/
  scarecrow-iterm-profile.json  # iTerm2 dynamic profile template
tests/
  test_app.py            # TUI integration tests
  test_behavioral.py     # behavioral contract tests
  test_integration.py    # real-model pipeline tests
  test_recorder.py       # audio recorder unit tests
  test_regressions.py    # regression tests for fixed bugs
  test_repo_policy.py    # repo policy enforcement tests
  test_session.py        # session/file management tests
  test_setup.py          # setup script tests
  test_summarizer.py     # summarizer unit tests
  test_startup.py        # startup smoke tests (imports, HF offline, model load)
  test_transcriber.py    # batch transcription tests
```
