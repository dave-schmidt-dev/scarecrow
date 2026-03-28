#!/bin/bash
# Simple GPU/CPU power monitor for Apple Silicon
# Usage: sudo ./benchmarks/gpu_monitor.sh [interval_ms]
#
# Shows GPU active residency %, GPU power, CPU power every interval

INTERVAL=${1:-2000}

sudo powermetrics \
  --samplers gpu_power,cpu_power \
  -i "$INTERVAL" \
  --format plist \
  2>/dev/null | \
while IFS= read -r line; do
  case "$line" in
    *"<key>gpu_active_residency</key>"*)
      read -r val
      val=$(echo "$val" | sed 's/[^0-9.]//g')
      printf "GPU active: %6.1f%%  |  " "$val"
      ;;
    *"<key>gpu_power</key>"*)
      read -r val
      val=$(echo "$val" | sed 's/[^0-9.]//g')
      printf "GPU: %5.0f mW  |  " "$val"
      ;;
    *"<key>combined_power</key>"*)
      read -r val
      val=$(echo "$val" | sed 's/[^0-9.]//g')
      printf "Total: %5.0f mW\n" "$val"
      ;;
  esac
done
