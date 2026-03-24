# Scarecrow

Always-recording TUI with live captions and transcription.

Scarecrow runs two Whisper models simultaneously: a fast model for real-time captions and a larger model for accurate batch transcription every 30 seconds. Audio and transcripts are saved per-session.

## Bug Tracking

Scarecrow keeps a persistent bug ledger in [BUGS.md](/Users/dave/Documents/Projects/scarecrow/BUGS.md). Future fixes must follow these rules:

- Append a `BUGS.md` entry whenever a bug is found, investigated, worked around, or fixed.
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
python3 scripts/sync_env.py
python scripts/setup.py   # interactive model selection + alias setup
```

The setup script explains the two-model architecture and walks you through choosing models.

### Virtualenv health

This project uses an editable install inside `.venv`. On this macOS setup, the editable-install path file can occasionally be recreated with the `UF_HIDDEN` flag during environment rebuilds, which breaks `import scarecrow` outside the project root.

Use these helpers instead of raw ad-hoc repairs:

```bash
python3 scripts/sync_env.py                 # uv sync + editable-install repair + import validation
python3 scripts/repair_venv.py              # repair/check existing .venv without syncing
python3 scripts/sync_env.py --reinstall-package scarecrow
```

If you still choose to run `uv sync` directly, follow it with `python3 scripts/repair_venv.py`.

### iTerm2 profile (recommended)

Scarecrow includes a dynamic iTerm2 profile (`Scarecrow`) with a larger font (Monaco 16pt) for readability. Copy the example profile to iTerm2's dynamic profiles directory:

```bash
cp examples/scarecrow-iterm-profile.json \
   ~/Library/Application\ Support/iTerm2/DynamicProfiles/scarecrow.json
```

Edit the file to update the path to your scarecrow project and `uv` binary.

Then add this alias to `~/.zshrc`:

```bash
alias sc='open -a iTerm && osascript -e "tell application \"iTerm2\" to create window with profile \"Scarecrow\""'
```

This opens a dedicated iTerm2 window that auto-runs scarecrow, shows errors on failure, and closes 3 seconds after a clean exit.

### Shell alias (alternative)

If you don't use iTerm2, add to `~/.zshrc` (or `~/.bashrc`):

```bash
alias sc="uv run --project /path/to/scarecrow scarecrow"
```

## Usage

```bash
sc          # start recording (auto-starts on launch)
```

**Keybindings** inside the TUI:
- `p` — pause / resume (releases microphone while paused)
- `q` — quit

### TUI layout

The TUI shows:
- **Info bar** — recording state (`REC` / `PAUSED`), mic indicator, elapsed time, word count, batch countdown
- **Transcript pane** — batch transcription output with timestamped dividers (upper, scrollable)
- **Live pane** — real-time captions from the fast model (lower, bordered; text promotes to stable scrolling lines every ~10s or at natural pauses, current partial updates in-place at bottom)
- **Footer** — keybindings

### Pause behavior

- Microphone is released (system mic indicator turns off)
- Buffered audio is transcribed immediately before pausing
- "Recording paused" markers are written to the transcript pane and file every 30s
- Elapsed timer continues running (tracks total session time)
- On resume, batch countdown resets for clean intervals

### Shutdown output

On quit (`q`), Scarecrow prints session metrics to the terminal:
- Recording duration
- Word count
- Session directory path
- Audio and transcript file sizes
- "Press Enter to close" prompt (auto-closes after 30s)

### Startup output

On launch, Scarecrow prints:
- Which models are loading (live + batch) with timing
- Model cache locations (or whether they need downloading)
- Where recordings and transcripts are saved

Models load in offline mode (`HF_HUB_OFFLINE=1`) to avoid network stalls. Debug logs are written to `~/.cache/scarecrow/debug.log`.

### Two-model architecture

| Role | Default model | Behaviour |
|------|--------------|-----------|
| **Live** (lower pane) | `base.en` | VAD-gated, transcribes during speech every ~1s, forced break every 10s |
| **Batch** (upper pane) | `medium.en` | Runs every 30s on buffered audio, produces accurate transcript |

A single 16kHz audio stream feeds both models. Silero VAD (ONNX, bundled) detects speech boundaries, triggering live transcription with base.en during speech. Continuous speech is force-broken every 10s so the live pane scrolls. Batch transcription with medium.en runs independently every 30 seconds. No subprocesses — everything runs in a single process with one worker thread.

Models are configured in `scarecrow/config.py` or via `scripts/setup.py`.

## Session files

Each recording session creates a timestamped directory:

```
recordings/
  2026-03-24_07-48-36/
    audio.wav          # full recording (16kHz PCM16)
    transcript.txt     # batch transcription with timestamped dividers
```

Audio is saved as uncompressed WAV (~1.8 MB/min at 16kHz mono) rather than MP3 (~120 KB/min). WAV writes raw PCM samples directly in the audio callback with zero CPU overhead — no encoder running in the hot path. Given that transcription models already demand significant CPU, this keeps the recording layer as lightweight as possible.

## Development

```bash
python3 scripts/sync_env.py          # install deps + repair editable install
uv run pytest                        # run tests
uv run ruff check scarecrow/ tests/  # lint
uv run vulture scarecrow/ vulture_whitelist.py --ignore-names inter_op_num_threads,intra_op_num_threads,log_severity_level  # dead code check
```

Pre-commit hooks run ruff (lint + format) and vulture automatically.

When fixing bugs, update `BUGS.md` and add or extend the matching regression test in the same change.

### Git hooks

Install the repo hooks after syncing the environment:

```bash
uv run pre-commit install --hook-type pre-commit --hook-type pre-push
```

The hooks enforce these repo rules:

- fail the commit if `README.md`, `HISTORY.md`, or `BUGS.md` is missing
- fail the commit if staged code changes do not include an update to `HISTORY.md`
- fail the commit if `BUGS.md` contains a squashed bug without a regression test reference
- run `ruff`, `pytest`, and `vulture` on pre-commit
- run the full `pytest` suite again on pre-push

## Architecture

```
scarecrow/
  __main__.py      # entry point, model loading, startup output
  app.py           # Textual TUI, batch transcription scheduling
  config.py        # model names, audio/VAD settings, defaults
  recorder.py      # sounddevice audio capture + WAV writing
  session.py       # timestamped session dirs + transcript files
  transcriber.py   # Silero VAD + faster-whisper live transcription
  models/          # bundled ONNX models (silero_vad.onnx)
  app.tcss         # TUI stylesheet
scripts/
  setup.py         # interactive first-time setup
examples/
  scarecrow-iterm-profile.json  # iTerm2 dynamic profile template
tests/
  test_app.py          # TUI integration tests
  test_behavioral.py   # behavioral contract tests
  test_recorder.py     # audio recorder unit tests
  test_session.py      # session/file management tests
  test_regressions.py  # regression tests for fixed bugs
  test_startup.py      # startup smoke tests (imports, .pth, HF offline, model load)
  test_transcriber.py  # VAD + transcription tests
```
