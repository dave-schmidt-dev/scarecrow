#!/usr/bin/env bash
# scarecrow_weekly_report.sh — launchd wrapper for the weekly Scarecrow report.
#
# Logs:
#   ~/Library/Logs/scarecrow-weekly-report/scarecrow-weekly-report.log
#   ~/Library/Logs/scarecrow-weekly-report/scarecrow-weekly-report-YYYYMMDD-HHMMSS.log

set -euo pipefail

LOCK_FILE="/tmp/scarecrow-weekly-report.lock"
LOG_DIR="${HOME}/Library/Logs/scarecrow-weekly-report"
AGG_LOG="${LOG_DIR}/scarecrow-weekly-report.log"
RUN_LOG="${LOG_DIR}/scarecrow-weekly-report-$(date '+%Y%m%d-%H%M%S').log"
STATE_DIR="${HOME}/Library/Application Support/scarecrow-weekly-report"
STATE_FILE="${STATE_DIR}/last-sent-week.txt"
EMAIL_STATE_DIR="${STATE_DIR}/email-alert-state"

SCARECROW_ROOT="/Users/dave/Documents/Projects/scarecrow"
SCARECROW_PY="${SCARECROW_ROOT}/.venv/bin/python"
REPORT_SCRIPT="${SCARECROW_ROOT}/scripts/report.py"
REPORT_DIR="/Users/dave/recordings"
EMAIL_ALERT_BIN="/Users/dave/Documents/Projects/email-alerts/bin/email-alert"

mkdir -p "${LOG_DIR}" "${STATE_DIR}" "${EMAIL_STATE_DIR}"

log() {
  local ts
  ts="$(date '+%Y-%m-%d %H:%M:%S %Z')"
  printf '[%s] %s\n' "$ts" "$*" >> "${AGG_LOG}"
}

wait_for_network() {
  for _ in {1..12}; do
    host -W 2 github.com >/dev/null 2>&1 && return 0
    sleep 5
  done
  return 1
}

target_week="$("${SCARECROW_PY}" - <<'PY'
from datetime import date, timedelta

today = date.today()
this_monday = today - timedelta(days=today.weekday())
last_monday = this_monday - timedelta(days=7)
iso = last_monday.isocalendar()
print(f"{iso.year}-W{iso.week:02d}")
PY
)"
report_path="${REPORT_DIR}/report_${target_week}.md"

exec 200>"${LOCK_FILE}"
if ! flock -n 200; then
  log "scarecrow: ${target_week} skip — previous run still active"
  exit 0
fi

if [[ -f "${STATE_FILE}" ]] && grep -qx "${target_week}" "${STATE_FILE}"; then
  log "scarecrow: ${target_week} skip — already sent"
  exit 0
fi

if ! wait_for_network; then
  log "scarecrow: ${target_week} DNS timeout after 60s; aborting."
  exit 1
fi

if [[ ! -x "${SCARECROW_PY}" ]]; then
  log "scarecrow: ${target_week} failed — missing python: ${SCARECROW_PY}"
  exit 1
fi

if [[ ! -x "${EMAIL_ALERT_BIN}" ]]; then
  log "scarecrow: ${target_week} failed — missing email-alert: ${EMAIL_ALERT_BIN}"
  exit 1
fi

log "scarecrow: ${target_week} starting"
start_epoch="$(date '+%s')"

if ! "${SCARECROW_PY}" "${REPORT_SCRIPT}" --last-week --output "${report_path}" >> "${RUN_LOG}" 2>&1; then
  rc=$?
  end_epoch="$(date '+%s')"
  elapsed=$(( end_epoch - start_epoch ))
  log "scarecrow: ${target_week} failed generating report with exit code ${rc} after ${elapsed}s"
  exit "${rc}"
fi

if [[ ! -s "${report_path}" ]]; then
  end_epoch="$(date '+%s')"
  elapsed=$(( end_epoch - start_epoch ))
  log "scarecrow: ${target_week} failed — report file missing or empty after ${elapsed}s"
  exit 1
fi

subject="Scarecrow Weekly Report — ${target_week}"
if ! EMAIL_ALERT_STATE_DIR="${EMAIL_STATE_DIR}" \
    "${EMAIL_ALERT_BIN}" --subject "${subject}" < "${report_path}" >> "${RUN_LOG}" 2>&1; then
  rc=$?
  end_epoch="$(date '+%s')"
  elapsed=$(( end_epoch - start_epoch ))
  log "scarecrow: ${target_week} failed sending email with exit code ${rc} after ${elapsed}s"
  exit "${rc}"
fi

printf '%s\n' "${target_week}" > "${STATE_FILE}"
end_epoch="$(date '+%s')"
elapsed=$(( end_epoch - start_epoch ))
log "scarecrow: ${target_week} completed successfully in ${elapsed}s"
log "scarecrow: ${target_week} email report sent"
