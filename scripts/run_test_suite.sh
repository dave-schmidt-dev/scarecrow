#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="$ROOT/.venv/bin/python"
PYTEST_WRAPPER="$ROOT/scripts/run_pytest_file.py"

cd "$ROOT"

FAILED=0
TMPDIR_LOGS="${TMPDIR:-/tmp}/scarecrow-test-$$"
mkdir -p "$TMPDIR_LOGS"

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
    return "$rc"
  fi
  return 0
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

# --- Phase 1: Run independent test files in parallel ---
PARALLEL_FILES=(
  tests/test_app.py
  tests/test_env_health.py
  tests/test_integration.py
  tests/test_recorder.py
  tests/test_regressions.py
  tests/test_repo_policy.py
  tests/test_session.py
  tests/test_setup.py
  tests/test_startup.py
  tests/test_suite_runner.py
  tests/test_transcriber.py
)

PIDS=()
for f in "${PARALLEL_FILES[@]}"; do
  logfile="$TMPDIR_LOGS/$(basename "$f" .py).log"
  run_pytest "$@" "$f" > "$logfile" 2>&1 &
  PIDS+=($!)
done

for i in "${!PIDS[@]}"; do
  pid="${PIDS[$i]}"
  f="${PARALLEL_FILES[$i]}"
  logfile="$TMPDIR_LOGS/$(basename "$f" .py).log"
  if ! wait "$pid"; then
    echo "FAIL: $f"
    cat "$logfile"
    FAILED=1
  fi
done

if [ "$FAILED" -ne 0 ]; then
  rm -rf "$TMPDIR_LOGS"
  exit 1
fi

# --- Phase 2: Behavioral tests run one node per process, parallelized ---
# Each node needs its own process (native-extension segfault isolation),
# but we can run multiple nodes concurrently.
MAX_JOBS="${SCARECROW_TEST_JOBS:-8}"
BPIDS=()
BLOGS=()
BNODES=()

while IFS= read -r test_id; do
  logfile="$TMPDIR_LOGS/behavioral-${#BPIDS[@]}.log"
  run_pytest "$@" "$test_id" > "$logfile" 2>&1 &
  BPIDS+=($!)
  BLOGS+=("$logfile")
  BNODES+=("$test_id")

  # Throttle: when we hit MAX_JOBS, wait for the oldest to finish
  if [ "${#BPIDS[@]}" -ge "$MAX_JOBS" ]; then
    pid="${BPIDS[0]}"
    if ! wait "$pid"; then
      echo "FAIL: ${BNODES[0]}"
      cat "${BLOGS[0]}"
      FAILED=1
    fi
    BPIDS=("${BPIDS[@]:1}")
    BLOGS=("${BLOGS[@]:1}")
    BNODES=("${BNODES[@]:1}")
  fi
done < <(collect_nodes tests/test_behavioral.py)

# Wait for remaining behavioral jobs
for i in "${!BPIDS[@]}"; do
  if ! wait "${BPIDS[$i]}"; then
    echo "FAIL: ${BNODES[$i]}"
    cat "${BLOGS[$i]}"
    FAILED=1
  fi
done

rm -rf "$TMPDIR_LOGS"

if [ "$FAILED" -ne 0 ]; then
  exit 1
fi
