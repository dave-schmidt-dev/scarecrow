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
  tests/test_sys_audio.py
  tests/test_audio_routing.py
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

PASSED=0
FAIL_FILES=()
PIDS=()
for f in "${TEST_FILES[@]}"; do
  logfile="$TMPDIR_LOGS/$(basename "$f" .py).log"
  run_pytest "$@" "$f" > "$logfile" 2>&1 &
  PIDS+=($!)
done

for i in "${!PIDS[@]}"; do
  pid="${PIDS[$i]}"
  f="${TEST_FILES[$i]}"
  logfile="$TMPDIR_LOGS/$(basename "$f" .py).log"
  if ! wait "$pid"; then
    echo "FAIL: $f"
    cat "$logfile"
    FAIL_FILES+=("$f")
    FAILED=1
  else
    PASSED=$((PASSED + 1))
  fi
done

rm -rf "$TMPDIR_LOGS"

echo ""
if [ "$FAILED" -ne 0 ]; then
  echo "${#FAIL_FILES[@]} failed, $PASSED passed (${#TEST_FILES[@]} files)"
  exit 1
else
  echo "$PASSED passed (${#TEST_FILES[@]} files)"
fi
