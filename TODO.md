# Open Issues

## Parakeet backend (feature/parakeet-mlx branch)
- Transcript pane shows each 5s batch on its own line — should space-join consecutive batches into paragraphs between dividers. RichLog.write() always creates a newline; need alternative approach (Static widget, or custom RichLog subclass).
- GPU usage is low (~1-2% duty cycle) despite transient spikes in Activity Monitor — confirmed via GPU History. No action needed.
- Parakeet does not support initial_prompt context injection — A/B test whether higher baseline accuracy compensates.
- Audio overlap disabled for parakeet (overlap_ms=0) to prevent repeated text at batch boundaries.

## Transcript accuracy
- condition_on_previous_text=False on whisper batch path (True caused inference slowdowns)
- Monitor: "surge" → "search" type errors on clean podcast audio — may need domain-specific prompts

## CPU usage
- 10% baseline is acceptable
- 30% spikes every 15s from batch transcription (large-v3-turbo on 15s audio) — expected
- Parakeet backend moves inference to GPU; CPU spikes eliminated

## Accessibility
- Screen reader support blocked on Textual framework (planned but not yet shipped)
- Monitor Textual releases for accessibility API; integrate when available

## Setup script
- Needs testing end-to-end with a fresh clone
