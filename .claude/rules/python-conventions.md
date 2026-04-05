---
paths:
  - "**/*.py"
---

# Python Conventions

## Linting
Ruff config is in `pyproject.toml`. A PostToolUse hook runs `ruff check --fix` and `ruff format` automatically on every Python edit. Do not add `# noqa` suppression without good reason.

## Package management
Never `pip install` or `uv pip install` anything. To add a dependency: add it to `pyproject.toml` then `uv sync`. To fix a broken venv: `rm -rf .venv && uv sync`.

## Running tests
NEVER run `pytest`, `uv run pytest`, or `python -m pytest` directly. This causes macOS CoreAudio/PortAudio segfault crash dialogs that interrupt the user.

Always use: `bash scripts/run_test_suite.sh`
Single file: `bash scripts/run_test_suite.sh tests/test_foo.py`

When instructing subagents, tell them explicitly: use `bash scripts/run_test_suite.sh`.

## Build model
This project uses a non-editable install. Source edits do NOT take effect until you rebuild:
```
uv sync --reinstall-package scarecrow --no-editable
```

## Target
Python 3.12+. Use modern syntax freely (match statements, f-string nesting, etc.).
