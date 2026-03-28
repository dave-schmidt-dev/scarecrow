<p align="center">
  <img src="assets/scarecrow-icon.svg" width="128" alt="Scarecrow">
</p>

# Scarecrow

Always-recording TUI for transcription and inline notes.

Scarecrow uses parakeet-mlx for accurate batch transcription. You can attach timestamped notes inline by typing prefix commands (`/task`) or plain text. Audio and transcripts are saved per-session.

**Backend:** parakeet-mlx (Parakeet TDT 0.6B v3) on Apple Silicon GPU — VAD-based chunking (drains at speech pauses), ~0.006x RTF, <1W GPU power draw

## Bug Tracking

Scarecrow keeps a persistent bug ledger in [BUGS.md](BUGS.md). Future fixes must follow these rules:

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
python scripts/setup.py   # show parakeet config info + alias setup
```

**Which launch method to use:**
- **iTerm2 users (recommended):** Use the iTerm2 profile — it handles font sizing, auto-close, and calls the venv binary directly.
- **Everyone else:** Add a shell alias pointing to `.venv/bin/scarecrow` (see Shell alias section below).
- **Avoid `uv run` in aliases** — it can re-trigger the macOS `UF_HIDDEN` flag on the editable-install `.pth` file.

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

Edit the file to update the path to your scarecrow project (replace `$HOME/path/to/scarecrow` with the actual path). The profile calls the venv binary directly instead of `uv run` to avoid the macOS `UF_HIDDEN` flag issue that `uv run` can trigger on the editable-install `.pth` file.

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

Avoid `uv run` in the alias — it can re-trigger the macOS `UF_HIDDEN` flag on the editable-install `.pth` file, breaking the import. If you must use `uv run`, prefix it with `chflags nohidden /path/to/scarecrow/.venv/lib/python3.12/site-packages/_scarecrow.pth 2>/dev/null;`.

## Usage

```bash
sc          # launch Scarecrow (auto-starts recording)
```

**Keybindings** inside the TUI:
- `Ctrl+P` — pause / resume (releases microphone while paused)
- `Ctrl+Q` — quit
- `Enter` — submit note (or, at startup, start recording)

**Commands** (type at the start of your note, then press Enter):
- `/task` or `/t` — submit note tagged `[TASK]`
- `/flush` or `/f` — force-flush the audio buffer (transcribe immediately)
- no prefix — submit note tagged `[NOTE]` (default)

**Help:**
- `/help`, `/h`, or `?` — show available commands and keybindings inline in the transcript pane

### TUI layout

The TUI shows:
- **Info bar** — recording state (`REC` / `PAUSED`), mic indicator with level label (quiet/low/med/high), elapsed time, word count, buffer/batch countdown, and an audio level meter (▁▂▃▄▅▆▇█) with color coding (green/yellow/red) using a log scale with peak-hold decay
- **Transcript pane** — batch transcription output with timestamped dividers (scrollable); every session begins with a `Session Start: YYYY-MM-DD HH:MM:SS` line and ends with a `Session End: YYYY-MM-DD HH:MM:SS` line
- **Notes pane** — text input for inline annotations; `/task` or `/t` prefix tags as `[TASK]`; plain text defaults to `[NOTE]`; notes are written to the transcript pane and transcript file with a wall-clock timestamp
- **Footer** — keybindings

### Pause behavior

- Microphone is released (system mic indicator turns off)
- Buffered audio is transcribed immediately before pausing
- A "Recording paused" marker is written to the transcript pane and file
- Elapsed timer continues running (tracks total session time)
- On resume, batch countdown resets for clean intervals

### Shutdown output

On quit (`Ctrl+Q`), Scarecrow prints session metrics to the terminal:
- Recording duration
- Word count
- Session directory path
- Audio and transcript file sizes
- "Press Enter to close" prompt (auto-closes after 30s)

On a clean quit, Scarecrow routes shutdown through `app.cleanup_after_exit()` to:
- stop microphone intake
- wait for any in-flight batch transcription to finish and capture its text directly from the future
- drain and transcribe the final buffered audio window
- abandon the batch executor if a worker times out, ignore late batch callbacks, and continue shutdown
- shut down the batch executor (non-blocking)
- flush and close the session transcript file

Ctrl+C uses the same cleanup path, so the final buffered batch is flushed before the session closes.

### Startup output

On launch, Scarecrow prints:
- Which model is loading with timing
- Where recordings and transcripts are saved

The parakeet model is preloaded during the prepare phase before the TUI launches, so the first batch transcription fires immediately without a cold-load delay. Models run in offline mode (`HF_HUB_OFFLINE=1`) to avoid network stalls. Debug logs are written to `~/.cache/scarecrow/debug.log`.

### Architecture

Scarecrow uses parakeet-mlx as its sole transcription engine. A 16kHz audio stream is buffered and fed to the model using VAD-based chunking — audio drains at natural speech pauses (600ms+ silence) with a 30-second hard max for continuous speech, polled every 150ms. No subprocesses — everything runs in a single process.

Inline notes are typed in the notes pane and submitted with Enter. The tag is determined by an optional prefix at the start of the text: `/task` or `/t` for `[TASK]`, or no prefix for `[NOTE]`. Each note is written to the transcript pane and the transcript file with a wall-clock timestamp and tag prefix. Notes work in any app state (recording, paused, or idle).

Transcript dividers show the start time of each audio batch window (not the time the model finishes processing). Dividers appear at most every 60 seconds. Consecutive batch results are joined into flowing paragraphs between dividers.

## Session files

Each recording session creates a timestamped directory:

```
recordings/
  2026-03-24_07-48-36/
    audio.wav          # full recording (16kHz PCM16)
    transcript.txt     # batch transcription with timestamped dividers; opens with "Session Start:" and closes with "Session End:"
```

Audio is saved as uncompressed WAV (~1.8 MB/min at 16kHz mono) rather than MP3 (~120 KB/min). WAV writes raw PCM samples directly in the audio callback with zero CPU overhead — no encoder running in the hot path. Given that transcription models already demand significant CPU, this keeps the recording layer as lightweight as possible.

## Development

```bash
python3 scripts/sync_env.py          # install deps + repair editable install
bash scripts/run_test_suite.sh       # run tests (isolated stable groups, including setup regressions)
uv run ruff check scarecrow/ tests/  # lint
uv run vulture scarecrow/ vulture_whitelist.py  # dead code check
```

**Do not run `pytest` directly** — always use `bash scripts/run_test_suite.sh`. The Textual async tests trigger a native segfault during interpreter teardown when run in a single pytest process. The suite runner isolates each test file (and each behavioral test node) in its own subprocess to avoid this.

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

## Troubleshooting

### Model download failures / offline mode

Scarecrow runs with `HF_HUB_OFFLINE=1` to avoid network stalls on launch. This means the model must already be cached locally. If you see a model-not-found error:

1. Run once without offline mode to download: `HF_HUB_OFFLINE=0 scarecrow` (or set the env var before launching).
2. Or download manually to `~/.cache/huggingface/hub/`.

### Microphone permission errors (macOS)

If the mic fails to open, grant Terminal or iTerm2 microphone access:

**System Settings > Privacy & Security > Microphone** — enable the terminal app you use.

### Virtualenv repair

If `import scarecrow` fails after running `uv sync` directly (e.g., the editable-install `.pth` file gets the `UF_HIDDEN` flag set):

```bash
python3 scripts/sync_env.py      # uv sync + repair + import validation
python3 scripts/repair_venv.py   # repair/check only, no sync
```

### Debug logs

Detailed error output is written to `~/.cache/scarecrow/debug.log`. Check this file if Scarecrow exits unexpectedly or transcription stops producing output.

## Architecture

```
scarecrow/
  __main__.py        # entry point, model preloading, startup output
  app.py             # Textual TUI, batch transcription scheduling, notes pane
  config.py          # audio settings, parakeet model config, defaults
  env_health.py      # editable-install .pth repair (macOS UF_HIDDEN)
  recorder.py        # sounddevice audio capture + WAV writing
  runtime.py         # HF offline bootstrap, parakeet model manager
  session.py         # timestamped session dirs + transcript files
  transcriber.py     # VAD-based parakeet-mlx batch transcription
  app.tcss           # TUI stylesheet
assets/
  scarecrow-icon.svg # app icon
bin/
  scarecrow          # wrapper script (sets PYTHONPATH, bypasses UF_HIDDEN)
scripts/
  setup.py           # parakeet config info + alias setup
  sync_env.py        # uv sync + editable-install repair
  repair_venv.py     # standalone .pth repair/validation
examples/
  scarecrow-iterm-profile.json  # iTerm2 dynamic profile template
tests/
  test_app.py            # TUI integration tests
  test_behavioral.py     # behavioral contract tests
  test_env_health.py     # editable-install repair tests
  test_integration.py    # real-model pipeline tests
  test_recorder.py       # audio recorder unit tests
  test_regressions.py    # regression tests for fixed bugs
  test_repo_policy.py    # repo policy enforcement tests
  test_session.py        # session/file management tests
  test_setup.py          # setup script tests
  test_startup.py        # startup smoke tests (imports, .pth, HF offline, model load)
  test_transcriber.py    # batch transcription tests
```
