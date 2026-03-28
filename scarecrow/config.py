"""Constants and default configuration."""

from pathlib import Path

# Audio settings
SAMPLE_RATE = 16000  # 16kHz — matches Whisper, no resampling needed
CHANNELS = 1
SUBTYPE = "PCM_16"

# Transcription models
FINAL_MODEL = "large-v3-turbo"  # Batch transcription model
LANGUAGE = "en"
BEAM_SIZE = 5
CONDITION_ON_PREVIOUS_TEXT = False

# Backend selection: "whisper" or "parakeet"
BACKEND = "parakeet"

# Parakeet settings (Apple Silicon GPU via MLX)
PARAKEET_MODEL = "mlx-community/parakeet-tdt-0.6b-v3"

# Batch intervals per backend (seconds)
BATCH_INTERVAL_WHISPER = 15
BATCH_INTERVAL_PARAKEET = 5

# VAD-based chunking (parakeet backend)
VAD_SILENCE_THRESHOLD = 0.01  # RMS below this counts as silence
VAD_MIN_SILENCE_MS = 300  # consecutive silence before triggering drain
VAD_MAX_BUFFER_SECONDS = 8  # hard drain if no silence found by this point
VAD_POLL_INTERVAL_MS = 150  # how often to check for silence

# Minimum seconds between transcript dividers
DIVIDER_INTERVAL = 60

# Storage
DEFAULT_RECORDINGS_DIR = Path.home() / "recordings"
