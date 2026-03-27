"""Constants and default configuration."""

from pathlib import Path

# Audio settings
SAMPLE_RATE = 16000  # 16kHz — matches Whisper, no resampling needed
CHANNELS = 1
SUBTYPE = "PCM_16"

# Transcription models
FINAL_MODEL = "medium.en"  # Accurate model for settled text (runs at sentence breaks)
LANGUAGE = "en"
BEAM_SIZE = 5
CONDITION_ON_PREVIOUS_TEXT = False

# Storage
DEFAULT_RECORDINGS_DIR = Path("recordings")
