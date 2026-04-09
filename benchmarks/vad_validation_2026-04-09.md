# VAD Config Validation (2026-04-09)

Fixed: replay_test.py now uses SYS_VAD_MAX_BUFFER_SECONDS (10s) for sys source.

## Lecture (120 min, single-speaker, Process Tap)

| Config | Drains | Avg Seg | WER | Seq Match | Word Ratio | Dropped | Novel |
|--------|--------|---------|-----|-----------|------------|---------|-------|
| Current (0.04/300/2.0) | 2976 | 2.1s | 0.075 | 0.942 | 1.01x | 46 | 158 |
| Sweep (0.008/1500/5.0) | 783 | 8.8s | 0.036 | 0.973 | 1.01x | 26 | 86 |
| Conservative (0.01/1000/2.0) | 935 | 7.3s | 0.040 | 0.971 | 1.01x | 24 | 84 |
| Compromise (0.01/1250/2.0) | 834 | 8.2s | 0.036 | 0.973 | 1.01x | 25 | 100 |

## Huddle (40 min, multi-speaker, Process Tap)

| Config | Drains | Avg Seg | WER | Seq Match | Word Ratio | Dropped | Novel |
|--------|--------|---------|-----|-----------|------------|---------|-------|
| Current (0.04/300/2.0) | 791 | 2.1s | 0.174 | 0.867 | 1.02x | 95 | 144 |
| Sweep (0.008/1500/5.0) | 301 | 6.8s | 0.099 | 0.927 | 1.02x | 53 | 73 |
| Conservative (0.01/1000/2.0) | 380 | 4.9s | 0.110 | 0.920 | 1.02x | 59 | 91 |
| Compromise (0.01/1250/2.0) | 340 | 5.6s | 0.104 | 0.924 | 1.03x | 56 | 79 |
