"""Constants and default configuration."""

from pathlib import Path

# Audio settings
SAMPLE_RATE = 44100
CHANNELS = 1
SUBTYPE = "PCM_16"

# Transcription models
REALTIME_MODEL = "tiny"  # Fast model for live preview
FINAL_MODEL = "small"  # Accurate model for settled text
LANGUAGE = "en"
BEAM_SIZE = 5
BEAM_SIZE_REALTIME = 3
REALTIME_PROCESSING_PAUSE = 0.2  # seconds between realtime updates

# Storage
DEFAULT_RECORDINGS_DIR = Path("recordings")
