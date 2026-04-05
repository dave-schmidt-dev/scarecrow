# VAD Tuning Results — 2026-04-05

## Test Data

Session: `2026-04-04_09-37-38_itn101-class-with-professor-isaac-davis`
Audio: `audio_sys.flac` (sys channel, 60 min, segment 1)
Reference: 30 x 2-min fixed windows, 6,864 words, Parakeet TDT 1.1B

## Baseline (current config)

```
SYS_VAD_SILENCE_THRESHOLD = 0.003
SYS_VAD_MIN_SILENCE_MS = 750
min_buffer_seconds = 5.0
```

## Sweep 1: silence_threshold (other params at current defaults)

| Threshold | Drains | Avg Seg | Seq Match | Word Ratio | Dropped | Novel |
|-----------|--------|---------|-----------|------------|---------|-------|
| 0.0005 | 536 | 6.7s | 0.916 | 1.03x | 50 | 109 |
| 0.001 | 561 | 6.4s | 0.916 | 1.02x | 54 | 113 |
| 0.002 | 584 | 6.2s | 0.913 | 1.03x | 56 | 126 |
| **0.003** | **608** | **5.9s** | **0.911** | **1.02x** | **57** | **124** |
| 0.005 | 621 | 5.8s | 0.908 | 1.03x | 54 | 123 |
| 0.008 | 641 | 5.6s | 0.905 | 1.02x | 56 | 137 |
| 0.01 | 661 | 5.4s | 0.904 | 1.02x | 55 | 129 |

**Winner: 0.001** (tied with 0.0005 at 0.916, but 0.001 is safer margin from true silence)

## Sweep 2: min_silence_ms (threshold=0.001, buffer=5.0s)

| min_silence_ms | Drains | Avg Seg | Seq Match | Word Ratio | Dropped | Novel |
|----------------|--------|---------|-----------|------------|---------|-------|
| 500 | 685 | 5.2s | 0.902 | 1.03x | 56 | 134 |
| 750 | 561 | 6.4s | 0.916 | 1.02x | 54 | 113 |
| 1000 | 461 | 7.8s | 0.920 | 1.02x | 41 | 101 |
| 1250 | 386 | 9.3s | 0.929 | 1.01x | 34 | 83 |
| 1500 | 333 | 10.8s | 0.932 | 1.01x | 45 | 82 |
| 1750 | 302 | 11.9s | 0.934 | 1.01x | 56 | 89 |
| 2000 | 276 | 13.0s | 0.933 | 1.01x | 42 | 87 |

**Winner: 1500** (0.932, plateau begins here; 1750 gains 0.002 but dropped vocab jumps to 56)

## Sweep 3: min_buffer_seconds (threshold=0.001, silence=1500ms)

| min_buffer | Drains | Avg Seg | Seq Match | Word Ratio | Dropped | Novel |
|------------|--------|---------|-----------|------------|---------|-------|
| 1s | 246 | 13.2s | 0.930 | 1.01x | 48 | 91 |
| 3s | 381 | 9.5s | 0.932 | 1.02x | 42 | 79 |
| 5s | 333 | 10.8s | 0.932 | 1.01x | 45 | 82 |
| 7s | 302 | 11.9s | 0.934 | 1.01x | 44 | 84 |
| 9s | 274 | 13.1s | 0.934 | 1.01x | 45 | 75 |
| 11s | 246 | 14.6s | 0.936 | 1.01x | 45 | 78 |

**Winner: 8s** (7-9 range is the plateau; 8s splits the difference)

## Head-to-head: old vs new (all three params changed)

| Metric | Old (0.003/750/5) | New (0.001/1500/8) | Change |
|--------|--------------------|--------------------|--------|
| Drains | 608 | 287 | -53% |
| Avg segment | 5.9s | 12.5s | +112% |
| Seq match | 0.911 | 0.932 | +2.1 pts |
| Word ratio | 1.02x | 1.01x | closer to 1.0 |
| Dropped vocab | 57 | 46 | -19% |
| Novel vocab | 124 | 82 | -34% |
| Wall time | 43.1s | 34.9s | -19% |

## Config changes applied

```python
# Before
SYS_VAD_SILENCE_THRESHOLD = 0.003
SYS_VAD_MIN_SILENCE_MS = 750
min_buffer_seconds = 5.0  # hardcoded in app.py

# After
SYS_VAD_SILENCE_THRESHOLD = 0.001
SYS_VAD_MIN_SILENCE_MS = 1500
SYS_VAD_MIN_BUFFER_SECONDS = 8.0  # promoted to config
```

## Notes

- Replay RMS is ~4x lower than live (FLAC stores post-gain audio). Absolute drain counts won't match live, but relative comparisons are valid.
- SequenceMatcher operates on word-level token lists (not character-level).
- Reference used `load_wav(target_rate=16000)` → float32 → Parakeet directly (no VAD).
- All sweeps used `--source sys` on the same 60-min audio file.
