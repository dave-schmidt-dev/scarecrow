"""Constants and default configuration."""

from pathlib import Path

# Audio settings
SAMPLE_RATE = 44100
CHANNELS = 1
SUBTYPE = "PCM_16"

# Transcription models
REALTIME_MODEL = "tiny.en"  # Fast model for live preview (runs constantly)
FINAL_MODEL = "medium.en"  # Accurate model for settled text (runs at sentence breaks)
LANGUAGE = "en"
BEAM_SIZE = 5
BEAM_SIZE_REALTIME = 3
REALTIME_PROCESSING_PAUSE = 0.2  # seconds between realtime updates

# Storage
DEFAULT_RECORDINGS_DIR = Path("recordings")
