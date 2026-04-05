# Scarecrow - Agent Instructions

Scarecrow is a Python TUI for always-on audio recording and transcription, built with Textual, Parakeet-MLX (ASR), and Gemma 4 26B MoE (summarizer).

## Key conventions
- **Test runner**: Never run `pytest` directly. Use `bash scripts/run_test_suite.sh`. See README.md for why.
- **Package management**: `uv sync` only. Never `pip install`. Broken venv: `rm -rf .venv && uv sync`.
- **Non-editable install**: Source edits require `uv sync --reinstall-package scarecrow --no-editable` to take effect.
- **Roadmap**: See `TODO.md` for open items.

## Development
See the **Development** section in `README.md` for complete build, test, lint, and commit workflows.
