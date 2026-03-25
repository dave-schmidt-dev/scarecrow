# Open Issues

## Live pane behavior
- ~~Text clears after 1-3 sentences~~ Fixed: single RichLog with stable lines + in-place partial
- ~~Partial text rendered outside bordered area~~ Fixed: removed two-widget split
- ~~Repeated words from overlapping 5s windows~~ Fixed: Apple Speech streaming (no overlap)
- ~~No scrolling during continuous speech~~ Fixed: Apple Speech emits word-by-word partials
- ~~Flicker on live pane updates~~ Fixed: batch_update() wraps clear+rewrite
- ~~Text tracking doesn't follow spoken words tightly~~ Fixed: Apple Speech latency < 300ms
- ~~No output at all in live pane~~ Fixed: `_on_realtime_update`/`_on_realtime_stabilized` now call UI methods directly; `call_from_thread` is rejected by Textual when called from app's own thread
- ~~Live pane stops updating mid-session~~ Fixed: natural `isFinal` sets `_needs_restart` flag; `tick()` starts new session after NSRunLoop pump returns (inline restart caused Speech framework reentrancy)
- ~~Live pane fills entire pane then clears on sentence boundary~~ Fixed: incremental commit every 10 uncommitted words flushes chunk to stable; 4-word tail kept as partial
- Live pane scroll resets at ~9-line boundary — BUG-20260325-live-pane-scroll-resets-at-boundary (open)

## Transcript accuracy
- condition_on_previous_text=False on batch path (True caused inference slowdowns)
- Monitor: "surge" → "search" type errors on clean podcast audio — may need domain-specific prompts

## CPU usage
- 10% baseline is acceptable
- 30% spikes every 30s from batch transcription (medium.en on 30s audio) — expected
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
