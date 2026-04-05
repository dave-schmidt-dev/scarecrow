#!/usr/bin/env bash
# Full VAD tuning sweep — generates references and runs all sweeps.
# Results appended to benchmarks/vad_tuning_2026-04-05.md
#
# Usage: bash scripts/vad_sweep.sh
# Expected runtime: ~30-40 minutes

set -euo pipefail

SESSION="/Users/dave/recordings/2026-04-04_09-37-38_itn101-class-with-professor-isaac-davis"
SYS_AUDIO="$SESSION/audio_sys.flac"
MIC_AUDIO="$SESSION/audio.flac"
RESULTS="benchmarks/vad_tuning_2026-04-05.md"
REPLAY="scripts/replay_test.py"

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
# Step 1: Generate mic reference (if not already present)
# -----------------------------------------------------------------------
if [ ! -f "$MIC_AUDIO.reference" ]; then
    echo ">>> Generating mic reference..."
    uv run python "$REPLAY" "$MIC_AUDIO" --save-reference 2>&1
    echo ""
else
    echo ">>> Mic reference already exists, skipping."
fi

# -----------------------------------------------------------------------
# Step 2: Sys fine-grained sweeps
# -----------------------------------------------------------------------
{
echo ""
echo "## Fine-grained sweep: sys silence_threshold (silence=1500ms, buffer=8s)"
echo ""
echo "| Threshold | Drains | Avg Seg | WER | Seq Match | Word Ratio | Dropped | Novel |"
echo "|-----------|--------|---------|-----|-----------|------------|---------|-------|"

for t in 0.0005 0.00075 0.001 0.00125 0.0015 0.00175 0.002; do
    run_compare "$t" "$SYS_AUDIO" --source sys --silence-threshold "$t" --min-silence-ms 1500 --min-buffer 8 --compare-reference
done

echo ""
echo "## Fine-grained sweep: sys min_silence_ms (threshold=0.001, buffer=8s)"
echo ""
echo "| min_silence_ms | Drains | Avg Seg | WER | Seq Match | Word Ratio | Dropped | Novel |"
echo "|----------------|--------|---------|-----|-----------|------------|---------|-------|"

for ms in 1250 1375 1500 1625 1750; do
    run_compare "$ms" "$SYS_AUDIO" --source sys --silence-threshold 0.001 --min-silence-ms "$ms" --min-buffer 8 --compare-reference
done

echo ""
echo "## Fine-grained sweep: sys min_buffer_seconds (threshold=0.001, silence=1500ms)"
echo ""
echo "| min_buffer | Drains | Avg Seg | WER | Seq Match | Word Ratio | Dropped | Novel |"
echo "|------------|--------|---------|-----|-----------|------------|---------|-------|"

for buf in 6 7 8 9 10; do
    run_compare "${buf}s" "$SYS_AUDIO" --source sys --silence-threshold 0.001 --min-silence-ms 1500 --min-buffer "$buf" --compare-reference
done

# -----------------------------------------------------------------------
# Step 3: Mic baseline + cross-test with sys-optimized values
# -----------------------------------------------------------------------
echo ""
echo "## Mic: baseline (current config) vs sys-optimized values"
echo ""
echo "| Config | Drains | Avg Seg | WER | Seq Match | Word Ratio | Dropped | Novel |"
echo "|--------|--------|---------|-----|-----------|------------|---------|-------|"

run_compare "current (0.01/750/0.5)" "$MIC_AUDIO" --source mic --compare-reference
run_compare "sys-opt (0.001/1500/8)" "$MIC_AUDIO" --source mic --silence-threshold 0.001 --min-silence-ms 1500 --min-buffer 8 --compare-reference

# -----------------------------------------------------------------------
# Step 4: Mic sweeps — threshold
# -----------------------------------------------------------------------
echo ""
echo "## Mic sweep: silence_threshold (silence=750ms, buffer=0.5s)"
echo ""
echo "| Threshold | Drains | Avg Seg | WER | Seq Match | Word Ratio | Dropped | Novel |"
echo "|-----------|--------|---------|-----|-----------|------------|---------|-------|"

for t in 0.001 0.003 0.005 0.007 0.009 0.011 0.013; do
    run_compare "$t" "$MIC_AUDIO" --source mic --silence-threshold "$t" --compare-reference
done

echo ""
echo "## Mic sweep: min_silence_ms (threshold=0.001, buffer=0.5s)"
echo ""
echo "| min_silence_ms | Drains | Avg Seg | WER | Seq Match | Word Ratio | Dropped | Novel |"
echo "|----------------|--------|---------|-----|-----------|------------|---------|-------|"

for ms in 500 750 1000 1250 1500 1750 2000; do
    run_compare "$ms" "$MIC_AUDIO" --source mic --silence-threshold 0.001 --min-silence-ms "$ms" --compare-reference
done

echo ""
echo "## Mic sweep: min_buffer_seconds (threshold=0.001, silence=1500ms)"
echo ""
echo "| min_buffer | Drains | Avg Seg | WER | Seq Match | Word Ratio | Dropped | Novel |"
echo "|------------|--------|---------|-----|-----------|------------|---------|-------|"

for buf in 1 3 5 7 9 11; do
    run_compare "${buf}s" "$MIC_AUDIO" --source mic --silence-threshold 0.001 --min-silence-ms 1500 --min-buffer "$buf" --compare-reference
done

} | tee -a "$RESULTS"

echo ""
echo "=== VAD Sweep complete at $(date) ==="
echo "Results appended to $RESULTS"
