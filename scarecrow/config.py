"""Constants and default configuration."""

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Config:
    """Application configuration.

    All fields have defaults matching the original module-level constants so
    existing ``config.FOO`` references continue to work via the module-level
    ``config`` instance below.  Constructors that need to be testable accept a
    ``Config`` parameter so tests can override values without monkeypatching
    globals.
    """

    # Audio settings
    SAMPLE_RATE: int = 16000  # 16kHz — required by parakeet-mlx
    RECORDING_SAMPLE_RATE: int = 48000  # 48kHz recording; downsampled to 16kHz for STT
    CHANNELS: int = 1
    SUBTYPE: str = "PCM_16"

    # Parakeet settings (Apple Silicon GPU via MLX)
    PARAKEET_MODEL: str = "mlx-community/parakeet-tdt-1.1b"

    # Batch interval (seconds)
    # Seeds UI countdown display only; VAD controls actual drain timing
    BATCH_INTERVAL: int = 5

    # Mic VAD — tuned via multi-session WER sweep (2026-04-05)
    # See benchmarks/vad_sweep_2026-04-05.md for full results
    MIC_GAIN: float = 1.0  # input gain multiplier (1.0 = no change)
    VAD_SILENCE_THRESHOLD: float = 0.003  # RMS below this counts as silence
    VAD_MIN_SILENCE_MS: int = 1250  # consecutive silence before triggering drain
    VAD_MAX_BUFFER_SECONDS: int = 30  # hard drain if no silence found by this point
    VAD_POLL_INTERVAL_MS: int = 150  # how often to check for silence
    VAD_MIN_SPEECH_RATIO: float = 0.15

    # System audio VAD — tuned via multi-session WER sweep (2026-04-05)
    # See benchmarks/vad_sweep_2026-04-05.md for full results
    SYS_GAIN: float = 0.25  # system audio gain — BlackHole is near full-scale
    SYS_VAD_SILENCE_THRESHOLD: float = 0.004  # tuned for pre-gain signal levels
    SYS_VAD_MIN_SILENCE_MS: int = 1500
    SYS_VAD_MIN_BUFFER_SECONDS: float = 7.0
    SYS_VAD_MIN_SPEECH_RATIO: float = 0.0  # disabled — no ambient noise to filter

    # System audio capture device
    SYSTEM_AUDIO_DEVICE: str = "BlackHole"  # substring match, case-insensitive

    # Writer thread queue size (bounded to prevent unbounded memory growth)
    # ~12.5 seconds of audio at 48kHz with 1024-sample blocks
    WRITER_QUEUE_SIZE: int = 600

    # Minimum seconds between transcript dividers
    DIVIDER_INTERVAL: int = 60

    # Auto-segmentation — rotate audio files at this interval
    SEGMENT_DURATION_SECONDS: int = 3600

    # Storage
    DEFAULT_RECORDINGS_DIR: Path = field(
        default_factory=lambda: Path.home() / "recordings"
    )

    # Obsidian sync — copy summaries to vault
    OBSIDIAN_VAULT_DIR: Path | None = field(
        default_factory=lambda: (
            Path.home()
            / "Library"
            / "Mobile Documents"
            / "iCloud~md~obsidian"
            / "Documents"
            / "Transcriptions Summaries"
        )
    )

    # Summarizer (local LLM — MLX via mlx-vlm, or GGUF via llama-cpp-python)
    SUMMARIZER_BACKEND: str = "mlx"  # "mlx" or "gguf"
    SUMMARIZER_MODEL_PATTERN: str = "*gemma-4-*-GGUF"  # GGUF fallback pattern
    SUMMARIZER_OUTPUT_BUDGET: int = 4096  # max tokens for summary output
    SUMMARIZER_CHARS_PER_TOKEN: int = 4  # rough heuristic for token estimation
    SUMMARIZER_MLX_MODEL_ID: str = "mlx-community/gemma-4-26b-a4b-it-4bit"
    SUMMARIZER_MLX_KV_BITS: int | None = None  # TurboQuant: 3-4 for compressed KV cache


# Module-level instance — all existing ``config.FOO`` references resolve here.
config = Config()

# ---------------------------------------------------------------------------
# Backwards-compatible module-level names
# Keep these so any code doing ``from scarecrow.config import SAMPLE_RATE``
# continues to work unchanged.
# ---------------------------------------------------------------------------
SAMPLE_RATE = config.SAMPLE_RATE
RECORDING_SAMPLE_RATE = config.RECORDING_SAMPLE_RATE
CHANNELS = config.CHANNELS
SUBTYPE = config.SUBTYPE
PARAKEET_MODEL = config.PARAKEET_MODEL
BATCH_INTERVAL = config.BATCH_INTERVAL
MIC_GAIN = config.MIC_GAIN
VAD_SILENCE_THRESHOLD = config.VAD_SILENCE_THRESHOLD
VAD_MIN_SILENCE_MS = config.VAD_MIN_SILENCE_MS
VAD_MAX_BUFFER_SECONDS = config.VAD_MAX_BUFFER_SECONDS
VAD_POLL_INTERVAL_MS = config.VAD_POLL_INTERVAL_MS
VAD_MIN_SPEECH_RATIO = config.VAD_MIN_SPEECH_RATIO
SYS_GAIN = config.SYS_GAIN
SYS_VAD_SILENCE_THRESHOLD = config.SYS_VAD_SILENCE_THRESHOLD
SYS_VAD_MIN_SILENCE_MS = config.SYS_VAD_MIN_SILENCE_MS
SYS_VAD_MIN_SPEECH_RATIO = config.SYS_VAD_MIN_SPEECH_RATIO
SYSTEM_AUDIO_DEVICE = config.SYSTEM_AUDIO_DEVICE
WRITER_QUEUE_SIZE = config.WRITER_QUEUE_SIZE
DIVIDER_INTERVAL = config.DIVIDER_INTERVAL
DEFAULT_RECORDINGS_DIR = config.DEFAULT_RECORDINGS_DIR
SUMMARIZER_BACKEND = config.SUMMARIZER_BACKEND
SUMMARIZER_MODEL_PATTERN = config.SUMMARIZER_MODEL_PATTERN
SUMMARIZER_OUTPUT_BUDGET = config.SUMMARIZER_OUTPUT_BUDGET
SUMMARIZER_CHARS_PER_TOKEN = config.SUMMARIZER_CHARS_PER_TOKEN
SUMMARIZER_MLX_MODEL_ID = config.SUMMARIZER_MLX_MODEL_ID
SUMMARIZER_MLX_KV_BITS = config.SUMMARIZER_MLX_KV_BITS
SEGMENT_DURATION_SECONDS = config.SEGMENT_DURATION_SECONDS
OBSIDIAN_VAULT_DIR = config.OBSIDIAN_VAULT_DIR
