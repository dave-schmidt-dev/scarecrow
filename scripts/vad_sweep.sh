#!/usr/bin/env bash
# Multi-session VAD tuning sweep with WER as primary metric.
#
# Runs parameter sweeps across diverse recordings to avoid overfitting
# to one speaker/room.
#
# Process Tap recordings (2026-04-08+): SYS_GAIN=1.0, FLAC = live values.
# No scaling needed — threshold values map directly to production config.
#
# Old BlackHole recordings (pre-2026-04-07): FLACs had 0.25x pre-gain,
# so threshold × 4 = live. Those sessions are no longer used.
#
# Usage: bash scripts/vad_sweep.sh
# Expected runtime: ~30-40 minutes (single session, sys-only)

set -euo pipefail

REPLAY="scripts/replay_test.py"
RESULTS="benchmarks/vad_sweep_$(date +%Y-%m-%d).md"

# Process Tap benchmark: 2-hour NVCC lecture (clean single speaker, normal volume)
SESSIONS=(
    "benchmarks/sys_vad_benchmark_2026-04-08.flac"
)
LABELS=("ITN213 NVCC lecture (Process Tap)")

run_compare() {
    local label="$1"; shift
    local summary
    summary=$(uv run python "$REPLAY" "$@" 2>&1 | grep -A20 "^Summary:\|^===")
    local drains avg_seg wer seq_match word_ratio dropped novel
    drains=$(echo "$summary" | grep "Segments:" | awk '{print $NF}')
    avg_seg=$(echo "$summary" | grep "Avg segment:" | awk '{print $NF}')
    wer=$(echo "$summary" | grep "WER:" | awk '{print $2}')
    seq_match=$(echo "$summary" | grep "Sequence match:" | awk '{print $NF}')
    word_ratio=$(echo "$summary" | grep "Word ratio:" | awk '{print $NF}')
    dropped=$(echo "$summary" | grep "Dropped vocab:" | awk '{print $(NF-1)}')
    novel=$(echo "$summary" | grep "Novel vocab:" | awk '{print $(NF-1)}')
    echo "| $label | $drains | $avg_seg | $wer | $seq_match | $word_ratio | $dropped | $novel |"
}

echo "=== VAD Sweep started at $(date) ==="
echo ""

# -----------------------------------------------------------------------
# Step 1: Generate references (if not already present)
# -----------------------------------------------------------------------
for i in "${!SESSIONS[@]}"; do
    SYS="${SESSIONS[$i]}"
    echo ">>> Session: ${LABELS[$i]}"
    if [ -f "$SYS" ] && [ ! -f "${SYS%.flac}.reference" ]; then
        echo "    Generating sys reference..."
        uv run python "$REPLAY" "$SYS" --source sys --save-reference 2>&1 | tail -3
    else
        echo "    Reference already exists, skipping."
    fi
    echo ""
done

{

# -----------------------------------------------------------------------
# Step 2: Sys silence_threshold sweep
# -----------------------------------------------------------------------
# Process Tap: FLAC = live values (SYS_GAIN=1.0, no scaling needed)
echo "## Sys sweep: silence_threshold (silence=750ms, buffer=5s)"
echo ""
echo "All sessions use conservative defaults for non-swept params."
echo "Process Tap recordings — threshold values map directly to live config."
echo ""

for i in "${!SESSIONS[@]}"; do
    SYS="${SESSIONS[$i]}"
    [ ! -f "$SYS" ] && continue
    echo ""
    echo "### ${LABELS[$i]}"
    echo ""
    echo "| Threshold | Drains | Avg Seg | WER | Seq Match | Word Ratio | Dropped | Novel |"
    echo "|-----------|--------|---------|-----|-----------|------------|---------|-------|"
    for t in 0.002 0.004 0.006 0.008 0.01 0.015 0.02 0.03 0.04; do
        run_compare "$t" "$SYS" --source sys --silence-threshold "$t" --min-silence-ms 750 --min-buffer 5 --compare-reference
    done
done

# -----------------------------------------------------------------------
# Step 3: Sys min_silence_ms sweep (threshold pinned from Step 2 winner)
# -----------------------------------------------------------------------
# NOTE: After running Step 2, update BEST_THRESHOLD below to the winner.
# Default 0.004 is a reasonable starting guess based on BlackHole-era results.
BEST_THRESHOLD="${BEST_THRESHOLD:-0.004}"
echo ""
echo "## Sys sweep: min_silence_ms (threshold=${BEST_THRESHOLD}, buffer=5s)"
echo ""
echo "Threshold fixed at ${BEST_THRESHOLD} (best from Step 2, or override via BEST_THRESHOLD env var)."
echo ""

for i in "${!SESSIONS[@]}"; do
    SYS="${SESSIONS[$i]}"
    [ ! -f "$SYS" ] && continue
    echo ""
    echo "### ${LABELS[$i]}"
    echo ""
    echo "| min_silence_ms | Drains | Avg Seg | WER | Seq Match | Word Ratio | Dropped | Novel |"
    echo "|----------------|--------|---------|-----|-----------|------------|---------|-------|"
    for ms in 300 500 750 1000 1250 1500 1750 2000; do
        run_compare "$ms" "$SYS" --source sys --silence-threshold "$BEST_THRESHOLD" --min-silence-ms "$ms" --min-buffer 5 --compare-reference
    done
done

# -----------------------------------------------------------------------
# Step 4: Sys min_buffer_seconds sweep
# -----------------------------------------------------------------------
# NOTE: After running Step 3, update BEST_SILENCE below to the winner.
BEST_SILENCE="${BEST_SILENCE:-1500}"
echo ""
echo "## Sys sweep: min_buffer_seconds (threshold=${BEST_THRESHOLD}, silence=${BEST_SILENCE}ms)"
echo ""

for i in "${!SESSIONS[@]}"; do
    SYS="${SESSIONS[$i]}"
    [ ! -f "$SYS" ] && continue
    echo ""
    echo "### ${LABELS[$i]}"
    echo ""
    echo "| min_buffer | Drains | Avg Seg | WER | Seq Match | Word Ratio | Dropped | Novel |"
    echo "|------------|--------|---------|-----|-----------|------------|---------|-------|"
    for buf in 2 3 5 7 9 11; do
        run_compare "${buf}s" "$SYS" --source sys --silence-threshold "$BEST_THRESHOLD" --min-silence-ms "$BEST_SILENCE" --min-buffer "$buf" --compare-reference
    done
done

# -----------------------------------------------------------------------
# Mic sweeps omitted — this benchmark session has no useful mic audio.
# Mic VAD thresholds from the 2026-04-05 sweep remain valid (mic gain
# unchanged by Process Tap migration).
# -----------------------------------------------------------------------

} | tee -a "$RESULTS"

echo ""
echo "=== VAD Sweep complete at $(date) ==="
echo "Results: $RESULTS"
