"""Constants and default configuration."""

from pathlib import Path

# Audio settings
SAMPLE_RATE = 16000  # 16kHz — matches Whisper, no resampling needed
CHANNELS = 1
SUBTYPE = "PCM_16"

# Transcription models
FINAL_MODEL = "large-v3-turbo"  # Accurate model for batch transcription
LANGUAGE = "en"
BEAM_SIZE = 5
CONDITION_ON_PREVIOUS_TEXT = False

# Storage
DEFAULT_RECORDINGS_DIR = Path("recordings")
