# Scarecrow

Always-recording TUI with live captions and transcription.

Scarecrow runs two Whisper models simultaneously: a fast model for real-time captions and a larger model for accurate batch transcription every 30 seconds. Audio and transcripts are saved per-session.

## Requirements

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) (recommended)
- macOS with microphone access

## Setup

```bash
git clone <repo-url> && cd scarecrow
uv sync
python scripts/setup.py   # interactive model selection + alias setup
```

The setup script explains the two-model architecture and walks you through choosing models.

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
- **Live pane** — real-time captions from the fast model (lower, bordered; stabilized lines scroll up, current partial updates in-place at bottom)
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

### Startup output

On launch, Scarecrow prints:
- Which models are loading (live + batch) with timing
- Model cache locations (or whether they need downloading)
- Where recordings and transcripts are saved

### Two-model architecture

| Role | Default model | Behaviour |
|------|--------------|-----------|
| **Live** (lower pane) | `base.en` | VAD-gated, transcribes during speech every ~1s |
| **Batch** (upper pane) | `medium.en` | Runs every 30s on buffered audio, produces accurate transcript |

A single 16kHz audio stream feeds both models. Silero VAD (ONNX, bundled) detects speech boundaries, triggering live transcription with base.en during speech. Batch transcription with medium.en runs independently every 30 seconds. No subprocesses — everything runs in a single process with one worker thread.

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
uv sync                              # install deps + dev tools
uv run pytest                        # run tests
uv run ruff check scarecrow/ tests/  # lint
uv run vulture scarecrow/            # dead code check
```

Pre-commit hooks run ruff (lint + format) and vulture automatically.

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
