#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="$ROOT/.venv/bin/python"
PYTEST_WRAPPER="$ROOT/scripts/run_pytest_file.py"

cd "$ROOT"

run_pytest() {
  env -i \
    HOME="$HOME" \
    PATH="$PATH" \
    TMPDIR="${TMPDIR:-/tmp}" \
    TERM="${TERM:-xterm-256color}" \
    LANG="${LANG:-en_US.UTF-8}" \
    LC_ALL="${LC_ALL:-en_US.UTF-8}" \
    "$PYTHON" "$PYTEST_WRAPPER" "$@"
}

run_pytest "$@" tests/test_app.py
run_pytest "$@" tests/test_behavioral.py
run_pytest "$@" tests/test_env_health.py
run_pytest "$@" tests/test_integration.py
run_pytest "$@" tests/test_recorder.py
run_pytest "$@" tests/test_regressions.py
run_pytest "$@" tests/test_repo_policy.py
run_pytest "$@" tests/test_session.py
run_pytest "$@" tests/test_startup.py
run_pytest "$@" tests/test_suite_runner.py
run_pytest "$@" tests/test_transcriber.py
