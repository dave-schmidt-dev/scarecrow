# Open Issues

## Transcript accuracy
- condition_on_previous_text=False on whisper batch path (True caused inference slowdowns)
- Monitor: "surge" → "search" type errors on clean podcast audio — may need domain-specific prompts
- Parakeet backend does not support initial_prompt context injection — A/B test whether its higher baseline accuracy compensates

## CPU usage
- 10% baseline is acceptable
- 30% spikes every 15s from batch transcription (large-v3-turbo on 15s audio) — expected
- Investigate: can batch run at lower priority or be deferred to reduce spike?

## Accessibility
- Screen reader support blocked on Textual framework (planned but not yet shipped)
- Monitor Textual releases for accessibility API; integrate when available

## Setup script
- Needs testing end-to-end with a fresh clone
