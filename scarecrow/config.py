"""Constants and default configuration."""

from pathlib import Path

# Audio settings
SAMPLE_RATE = 16000  # 16kHz — matches Whisper, no resampling needed
CHANNELS = 1
SUBTYPE = "PCM_16"

# Transcription models
REALTIME_MODEL = "tiny.en"  # Fast model for live preview (runs constantly)
FINAL_MODEL = "medium.en"  # Accurate model for settled text (runs at sentence breaks)
LANGUAGE = "en"
BEAM_SIZE = 5
BEAM_SIZE_REALTIME = 3
REALTIME_PROCESSING_PAUSE = 0.2  # seconds between realtime updates

# VAD settings (Silero VAD, ONNX)
VAD_THRESHOLD = 0.5  # speech probability above this = speech
VAD_NEG_THRESHOLD = 0.35  # speech probability below this = silence
VAD_PRE_BUFFER_SECONDS = 1.0  # audio buffered before speech detected
VAD_MIN_SPEECH_SECONDS = 0.5  # minimum utterance length to transcribe
VAD_SILENCE_SECONDS = 0.6  # silence duration to end utterance
VAD_CHUNK_SAMPLES = 512  # Silero requires exactly 512 at 16kHz

# Storage
DEFAULT_RECORDINGS_DIR = Path("recordings")
