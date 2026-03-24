# Open Issues

## Live pane behavior
- Text clears after 1-3 sentences — partial updates rewrite history, needs smoother scrolling
- Stabilized text appends correctly but partials disrupt the visual flow
- Text tracking doesn't follow spoken words tightly (1s transcription interval)

## CPU usage
- 10% baseline is acceptable
- 30% spikes every 30s from batch transcription (medium.en on 30s audio) — expected
- Occasional 50% spikes when live tiny.en and batch medium.en overlap
- Investigate: can batch run at lower priority or be deferred if live is active?

## VAD tuning
- Current thresholds: speech=0.5, silence=0.35, end=0.6s, min_speech=0.5s
- May need adjustment based on testing with different environments and voices
- Pre-buffer is 1.0s — verify speech onset is captured cleanly

## Pause/resume
- Needs thorough testing after RealtimeSTT replacement
- Mic release on pause (stream.stop) — verify system mic indicator turns off
- Resume latency — verify stream.start() is fast enough
- Batch timer behavior during pause — should print "Recording paused" markers

## Startup performance
- "Loading models" phase takes ~2s (faster-whisper model init)
- "Importing libraries" delay is mainly faster-whisper + onnxruntime imports
- Investigate lazy loading or background model init

## Setup script
- `scripts/setup.py` references old model cache path logic (dots vs dashes)
- Needs testing end-to-end with a fresh clone
