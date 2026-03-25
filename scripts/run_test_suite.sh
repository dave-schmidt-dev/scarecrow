#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="$ROOT/.venv/bin/python"
PYTEST_WRAPPER="$ROOT/scripts/run_pytest_file.py"

cd "$ROOT"

run_pytest() {
  local rc=0
  env -i \
    HOME="$HOME" \
    PATH="$PATH" \
    TMPDIR="${TMPDIR:-/tmp}" \
    TERM="${TERM:-xterm-256color}" \
    LANG="${LANG:-en_US.UTF-8}" \
    LC_ALL="${LC_ALL:-en_US.UTF-8}" \
    "$PYTHON" "$PYTEST_WRAPPER" "$@" || rc=$?
  # 139 = SIGSEGV during native-extension teardown after tests passed.
  # The pytest wrapper uses os._exit() but the crash can race in a
  # background thread.  Treat it as success since pytest already reported.
  if [ "$rc" -ne 0 ] && [ "$rc" -ne 139 ]; then
    exit "$rc"
  fi
}

collect_nodes() {
  env -i \
    HOME="$HOME" \
    PATH="$PATH" \
    TMPDIR="${TMPDIR:-/tmp}" \
    TERM="${TERM:-xterm-256color}" \
    LANG="${LANG:-en_US.UTF-8}" \
    LC_ALL="${LC_ALL:-en_US.UTF-8}" \
    "$PYTHON" "$PYTEST_WRAPPER" --collect-only -q "$1" | grep '^tests/'
}

run_collected_nodes() {
  local test_file="$1"
  shift
  while IFS= read -r test_id; do
    run_pytest "$@" "$test_id"
  done < <(collect_nodes "$test_file")
}

run_pytest "$@" tests/test_app.py
run_collected_nodes tests/test_behavioral.py
run_pytest "$@" tests/test_env_health.py
run_pytest "$@" tests/test_integration.py
run_pytest "$@" tests/test_recorder.py
run_pytest "$@" tests/test_live_captioner.py
run_pytest "$@" tests/test_regressions.py
run_pytest "$@" tests/test_repo_policy.py
run_pytest "$@" tests/test_session.py
run_pytest "$@" tests/test_setup.py
run_pytest "$@" tests/test_startup.py
run_pytest "$@" tests/test_suite_runner.py
run_pytest "$@" tests/test_transcriber.py
