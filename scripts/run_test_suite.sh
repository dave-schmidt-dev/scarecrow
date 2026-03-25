#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="$ROOT/.venv/bin/python"

cd "$ROOT"

"$PYTHON" -m pytest "$@" tests/test_app.py tests/test_behavioral.py
"$PYTHON" -m pytest "$@" tests/test_env_health.py
"$PYTHON" -m pytest "$@" tests/test_integration.py
"$PYTHON" -m pytest "$@" tests/test_recorder.py
"$PYTHON" -m pytest "$@" tests/test_regressions.py
"$PYTHON" -m pytest "$@" tests/test_repo_policy.py
"$PYTHON" -m pytest "$@" tests/test_session.py
"$PYTHON" -m pytest "$@" tests/test_startup.py
"$PYTHON" -m pytest "$@" tests/test_suite_runner.py
"$PYTHON" -m pytest "$@" tests/test_transcriber.py
