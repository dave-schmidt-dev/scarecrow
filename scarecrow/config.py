"""Constants and default configuration."""

from pathlib import Path

# Audio settings
SAMPLE_RATE = 16000  # 16kHz — required by parakeet-mlx
CHANNELS = 1
SUBTYPE = "PCM_16"

# Parakeet settings (Apple Silicon GPU via MLX)
PARAKEET_MODEL = "mlx-community/parakeet-tdt-0.6b-v3"

# Batch interval (seconds)
BATCH_INTERVAL = 5

# VAD-based chunking — tuned 2026-03-28 to reduce chunk-boundary errors:
#   MIN_SILENCE_MS 300→600: only drain on real sentence-ending pauses,
#     not brief mid-sentence hesitations
#   MAX_BUFFER_SECONDS 8→30: avoid forced mid-speech splits; parakeet
#     handles long audio fine and M-series has plenty of memory
#   overlap removed entirely: was a whisper-era concept that caused
#     duplicate text with parakeet (see commit 70f871c)
VAD_SILENCE_THRESHOLD = 0.01  # RMS below this counts as silence
VAD_MIN_SILENCE_MS = 600  # consecutive silence before triggering drain
VAD_MAX_BUFFER_SECONDS = 30  # hard drain if no silence found by this point
VAD_POLL_INTERVAL_MS = 150  # how often to check for silence

# Minimum seconds between transcript dividers
DIVIDER_INTERVAL = 60

# Storage
DEFAULT_RECORDINGS_DIR = Path.home() / "recordings"
