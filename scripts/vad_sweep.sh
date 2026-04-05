#!/usr/bin/env bash
# Multi-session VAD tuning sweep with WER as primary metric.
#
# Runs parameter sweeps across diverse recordings to avoid overfitting
# to one speaker/room. Old FLACs contain post-gain sys audio (0.25x),
# so silence_threshold values here are 4x lower than live equivalents.
# Multiply final threshold by 4 for production config.
#
# Usage: bash scripts/vad_sweep.sh
# Expected runtime: ~90-120 minutes

set -euo pipefail

REPLAY="scripts/replay_test.py"
RESULTS="benchmarks/vad_sweep_$(date +%Y-%m-%d).md"

# Diverse session set: lecture, multi-person call, phone call
SESSIONS=(
    "/Users/dave/recordings/2026-04-04_09-37-38_itn101-class-with-professor-isaac-davis"
    "/Users/dave/recordings/2026-04-03_16-14-24_signal-call-wmike-and-justin"
    "/Users/dave/recordings/2026-04-03_08-11-36_optumrx-pharmacy-prior-authorization-call"
)
LABELS=("ITN101 lecture" "Signal group call" "OptumRX phone call")

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
    SYS="${SESSIONS[$i]}/audio_sys.flac"
    MIC="${SESSIONS[$i]}/audio.flac"
    echo ">>> Session: ${LABELS[$i]}"
    if [ -f "$SYS" ] && [ ! -f "${SYS}.reference" ]; then
        echo "    Generating sys reference..."
        uv run python "$REPLAY" "$SYS" --save-reference 2>&1 | tail -3
    fi
    if [ -f "$MIC" ] && [ ! -f "${MIC}.reference" ]; then
        echo "    Generating mic reference..."
        uv run python "$REPLAY" "$MIC" --save-reference 2>&1 | tail -3
    fi
    echo ""
done

{

# -----------------------------------------------------------------------
# Step 2: Sys silence_threshold sweep
# -----------------------------------------------------------------------
# Old FLACs are 0.25x, so these map to live values 4x higher:
#   0.0005 → 0.002, 0.001 → 0.004, 0.002 → 0.008, 0.003 → 0.012, 0.004 → 0.016
echo "## Sys sweep: silence_threshold (silence=750ms, buffer=5s)"
echo ""
echo "All sessions use conservative defaults for non-swept params."
echo "Threshold values are FLAC-scale (multiply by 4 for live config)."
echo ""

for i in "${!SESSIONS[@]}"; do
    SYS="${SESSIONS[$i]}/audio_sys.flac"
    [ ! -f "$SYS" ] && continue
    echo ""
    echo "### ${LABELS[$i]}"
    echo ""
    echo "| Threshold (FLAC) | Drains | Avg Seg | WER | Seq Match | Word Ratio | Dropped | Novel |"
    echo "|------------------|--------|---------|-----|-----------|------------|---------|-------|"
    for t in 0.0005 0.00075 0.001 0.0015 0.002 0.0025 0.003 0.004; do
        run_compare "$t" "$SYS" --source sys --silence-threshold "$t" --min-silence-ms 750 --min-buffer 5 --compare-reference
    done
done

# -----------------------------------------------------------------------
# Step 3: Sys min_silence_ms sweep (using threshold=0.00075, the live 0.003)
# -----------------------------------------------------------------------
echo ""
echo "## Sys sweep: min_silence_ms (threshold=0.00075, buffer=5s)"
echo ""
echo "Threshold fixed at 0.00075 FLAC-scale (= 0.003 live)."
echo ""

for i in "${!SESSIONS[@]}"; do
    SYS="${SESSIONS[$i]}/audio_sys.flac"
    [ ! -f "$SYS" ] && continue
    echo ""
    echo "### ${LABELS[$i]}"
    echo ""
    echo "| min_silence_ms | Drains | Avg Seg | WER | Seq Match | Word Ratio | Dropped | Novel |"
    echo "|----------------|--------|---------|-----|-----------|------------|---------|-------|"
    for ms in 500 750 1000 1250 1500 1750 2000; do
        run_compare "$ms" "$SYS" --source sys --silence-threshold 0.00075 --min-silence-ms "$ms" --min-buffer 5 --compare-reference
    done
done

# -----------------------------------------------------------------------
# Step 4: Sys min_buffer_seconds sweep
# -----------------------------------------------------------------------
echo ""
echo "## Sys sweep: min_buffer_seconds (threshold=0.00075, silence=750ms)"
echo ""

for i in "${!SESSIONS[@]}"; do
    SYS="${SESSIONS[$i]}/audio_sys.flac"
    [ ! -f "$SYS" ] && continue
    echo ""
    echo "### ${LABELS[$i]}"
    echo ""
    echo "| min_buffer | Drains | Avg Seg | WER | Seq Match | Word Ratio | Dropped | Novel |"
    echo "|------------|--------|---------|-----|-----------|------------|---------|-------|"
    for buf in 3 5 7 9 11; do
        run_compare "${buf}s" "$SYS" --source sys --silence-threshold 0.00075 --min-silence-ms 750 --min-buffer "$buf" --compare-reference
    done
done

# -----------------------------------------------------------------------
# Step 5: Mic silence_threshold sweep (no gain issue)
# -----------------------------------------------------------------------
echo ""
echo "## Mic sweep: silence_threshold (silence=750ms, buffer=0.5s)"
echo ""
echo "Mic FLAC gain=1.0, no scaling needed. Values map directly to live config."
echo ""

for i in "${!SESSIONS[@]}"; do
    MIC="${SESSIONS[$i]}/audio.flac"
    [ ! -f "$MIC" ] && continue
    echo ""
    echo "### ${LABELS[$i]}"
    echo ""
    echo "| Threshold | Drains | Avg Seg | WER | Seq Match | Word Ratio | Dropped | Novel |"
    echo "|-----------|--------|---------|-----|-----------|------------|---------|-------|"
    for t in 0.001 0.003 0.005 0.007 0.01 0.013 0.015; do
        run_compare "$t" "$MIC" --source mic --silence-threshold "$t" --compare-reference
    done
done

# -----------------------------------------------------------------------
# Step 6: Mic min_silence_ms sweep
# -----------------------------------------------------------------------
echo ""
echo "## Mic sweep: min_silence_ms (threshold=0.01, buffer=0.5s)"
echo ""

for i in "${!SESSIONS[@]}"; do
    MIC="${SESSIONS[$i]}/audio.flac"
    [ ! -f "$MIC" ] && continue
    echo ""
    echo "### ${LABELS[$i]}"
    echo ""
    echo "| min_silence_ms | Drains | Avg Seg | WER | Seq Match | Word Ratio | Dropped | Novel |"
    echo "|----------------|--------|---------|-----|-----------|------------|---------|-------|"
    for ms in 500 750 1000 1250 1500 1750 2000; do
        run_compare "$ms" "$MIC" --source mic --silence-threshold 0.01 --min-silence-ms "$ms" --compare-reference
    done
done

} | tee -a "$RESULTS"

echo ""
echo "=== VAD Sweep complete at $(date) ==="
echo "Results: $RESULTS"
