# Open Issues

## Transcript accuracy
- condition_on_previous_text=False on batch path (True caused inference slowdowns)
- Monitor: "surge" → "search" type errors on clean podcast audio — may need domain-specific prompts

## CPU usage
- 10% baseline is acceptable
- 30% spikes every 15s from batch transcription (medium.en on 15s audio) — expected
- Investigate: can batch run at lower priority or be deferred to reduce spike?

## Pause/resume
- Mic release on pause (stream.stop) — verify system mic indicator turns off
- Resume latency — verify stream.start() is fast enough
- Batch timer behavior during pause — should print "Recording paused" markers

## Startup performance
- ~~HF Hub network stall: 30-60s delay on model load~~ Fixed: HF_HUB_OFFLINE=1
- ~~tqdm crash inside Textual: killed all transcription silently~~ Fixed: pre-init tqdm lock before TUI
- Debug log moved to ~/.cache/scarecrow/debug.log (was CWD-relative, lost when iTerm launches from ~)

## Setup script
- ~~`scripts/setup.py` references old model cache path logic (dots vs dashes)~~ Fixed: `check_cached` now uses the model name directly
- Needs testing end-to-end with a fresh clone
