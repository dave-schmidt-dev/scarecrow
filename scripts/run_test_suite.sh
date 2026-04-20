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
  env -i \
    HOME="$HOME" \
    PATH="$PATH" \
    TMPDIR="${TMPDIR:-/tmp}" \
    TERM="${TERM:-xterm-256color}" \
    LANG="${LANG:-en_US.UTF-8}" \
    LC_ALL="${LC_ALL:-en_US.UTF-8}" \
    "$PYTHON" "$PYTEST_WRAPPER" "$@"
}

# --- Run all test files in parallel ---
TEST_FILES=(
  tests/test_app.py
  tests/test_echo_filter.py
  tests/test_jsonl_schema.py
  tests/test_recorder.py
  tests/test_repo_policy.py
  tests/test_session.py
  tests/test_setup.py
  tests/test_startup.py
  tests/test_suite_runner.py
  tests/test_summarizer.py
  tests/test_summary_review.py
  tests/test_task_review.py
  tests/test_sys_audio.py
  tests/test_audio_tap.py
  tests/test_pipeline.py
  tests/test_transcriber.py
  tests/test_app_infobar.py
  tests/test_app_notes.py
  tests/test_app_shutdown.py
  tests/test_app_recording.py
  tests/test_app_vad_events.py
  tests/test_app_sys_audio.py
  tests/test_app_mute_controls.py
  tests/test_app_sys_vad.py
  tests/test_app_context_menu.py
  tests/test_diarizer.py
  tests/test_report.py
)

PER_TEST_TIMEOUT=120  # seconds per test file
MAX_PARALLEL=${SCARECROW_TEST_JOBS:-8}  # cap concurrent test processes

PASSED=0
FAIL_FILES=()

# Arrays to track the current batch of running jobs
BATCH_PIDS=()
BATCH_FILES=()
BATCH_LOGS=()

collect_job() {
  # Collect result for a single job by index into BATCH_* arrays
  local idx="$1"
  local pid="${BATCH_PIDS[$idx]}"
  local f="${BATCH_FILES[$idx]}"
  local logfile="${BATCH_LOGS[$idx]}"

  local elapsed=0 rc=""
  while [ $elapsed -lt $PER_TEST_TIMEOUT ]; do
    if ! kill -0 "$pid" 2>/dev/null; then
      wait "$pid" 2>/dev/null && rc=0 || rc=$?
      break
    fi
    sleep 1
    elapsed=$((elapsed + 1))
  done

  if [ -z "$rc" ]; then
    kill "$pid" 2>/dev/null
    wait "$pid" 2>/dev/null || true
    echo "TIMEOUT: $f (>${PER_TEST_TIMEOUT}s)"
    FAIL_FILES+=("$f")
    FAILED=1
  elif [ "$rc" -ne 0 ]; then
    echo "FAIL: $f"
    cat "$logfile"
    FAIL_FILES+=("$f")
    FAILED=1
  else
    PASSED=$((PASSED + 1))
  fi
}

# Launch test files in batches of MAX_PARALLEL
for f in "${TEST_FILES[@]}"; do
  logfile="$TMPDIR_LOGS/$(basename "$f" .py).log"
  run_pytest "$@" "$f" > "$logfile" 2>&1 &
  BATCH_PIDS+=($!)
  BATCH_FILES+=("$f")
  BATCH_LOGS+=("$logfile")

  # When we hit the cap, wait for all current jobs before launching more
  if [ "${#BATCH_PIDS[@]}" -ge "$MAX_PARALLEL" ]; then
    for i in "${!BATCH_PIDS[@]}"; do
      collect_job "$i"
    done
    BATCH_PIDS=()
    BATCH_FILES=()
    BATCH_LOGS=()
  fi
done

# Collect any remaining jobs from the final batch
for i in "${!BATCH_PIDS[@]}"; do
  collect_job "$i"
done

rm -rf "$TMPDIR_LOGS"

echo ""
if [ "$FAILED" -ne 0 ]; then
  echo "${#FAIL_FILES[@]} failed, $PASSED passed (${#TEST_FILES[@]} files)"
  exit 1
else
  echo "$PASSED passed (${#TEST_FILES[@]} files)"
fi
