"""Constants and default configuration."""

from pathlib import Path

# Audio settings
SAMPLE_RATE = 16000  # 16kHz — required by parakeet-mlx
CHANNELS = 1
SUBTYPE = "PCM_16"

# Parakeet settings (Apple Silicon GPU via MLX)
PARAKEET_MODEL = "mlx-community/parakeet-tdt-0.6b-v3"

# Batch interval (seconds)
# Seeds UI countdown display only; VAD controls actual drain timing
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

# Writer thread queue size (bounded to prevent unbounded memory growth)
# ~12.5 seconds of audio at 16kHz with 1024-sample blocks
WRITER_QUEUE_SIZE = 200

# Minimum seconds between transcript dividers
DIVIDER_INTERVAL = 60

# Storage
DEFAULT_RECORDINGS_DIR = Path.home() / "recordings"

# Summarizer (local LLM via llama-server)
SUMMARIZER_MODEL_PATTERN = "*Nemotron*Nano*GGUF"
SUMMARIZER_SERVER_ALIAS = "scarecrow-summarizer"
SUMMARIZER_PORT_RANGE = (8100, 8999)
SUMMARIZER_SERVER_TIMEOUT = 180  # seconds to wait for server readiness
SUMMARIZER_MAX_RETRIES = 2
SUMMARIZER_OUTPUT_BUDGET = 4096  # max tokens for summary output
SUMMARIZER_MIN_CTX = 131072  # 128K floor — no memory cost on short sessions (Mamba-2)
SUMMARIZER_CHARS_PER_TOKEN = 4  # rough heuristic for token estimation
