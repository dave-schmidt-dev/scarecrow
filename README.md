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

Scarecrow includes a dynamic iTerm2 profile (`Scarecrow`) with a larger font (Monaco 16pt) for readability. It's installed automatically via `~/Library/Application Support/iTerm2/DynamicProfiles/scarecrow.json`.

Add this alias to `~/.zshrc`:

```bash
alias sc='open -a iTerm && osascript -e "tell application \"iTerm2\" to create window with profile \"Scarecrow\""'
```

This opens a dedicated iTerm2 window with the Scarecrow profile, which auto-runs the app and closes the window on exit.

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
- `p` — pause / resume
- `q` — quit

### TUI layout

The TUI shows:
- **Info bar** — recording state (`REC`/`PAUSED`), elapsed time, word count, batch countdown
- **Audio meter** — real-time microphone level with block characters
- **Audio sparkline** — rolling history of audio levels
- **Transcript pane** — batch transcription output (upper, scrollable)
- **Live pane** — real-time captions from the fast model (lower, fixed height)
- **Footer** — keybindings

### Startup output

On launch, Scarecrow prints:
- Which models are loading (live + batch) with timing
- Model cache locations (or whether they need downloading)
- Where recordings and transcripts are saved

### Two-model architecture

| Role | Default model | Behaviour |
|------|--------------|-----------|
| **Live** (lower pane) | `tiny.en` | Runs continuously, shows real-time captions |
| **Batch** (upper pane) | `medium.en` | Runs every 30s on buffered audio, produces accurate transcript |

A single 16kHz audio stream feeds both models. Silero VAD (ONNX) detects speech boundaries, triggering live transcription with tiny.en during speech and accurate batch transcription with medium.en every 30 seconds. No subprocesses — everything runs in a single process with one worker thread.

Models are configured in `scarecrow/config.py` or via `scripts/setup.py`.

## Session files

Each recording session creates a timestamped directory:

```
recordings/
  2026-03-24_07-48-36/
    audio.wav          # full recording (16kHz PCM16)
    transcript.txt     # batch transcription output, with timestamped dividers
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
  config.py        # model names, audio settings, defaults
  recorder.py      # sounddevice audio capture + WAV writing
  session.py       # timestamped session dirs + transcript files
  transcriber.py   # Silero VAD + faster-whisper live transcription
  models/          # Bundled ONNX models (silero_vad.onnx)
  app.tcss         # TUI stylesheet
scripts/
  setup.py         # interactive first-time setup
tests/
  test_app.py      # TUI integration tests
  test_recorder.py # audio recorder unit tests
  test_session.py  # session/file management tests
  test_regressions.py  # regression tests for fixed bugs
```
