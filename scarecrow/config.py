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

    # System audio VAD — Process Tap values (2026-04-07).
    # Process Tap level varies with system volume slider, so absolute RMS
    # thresholds are inherently fragile. These are set high enough to work
    # at normal listening volumes (~50-75%). TODO: relative energy VAD.
    SYS_GAIN: float = 1.0  # Process Tap signal is well-scaled; no attenuation needed
    SYS_VAD_SILENCE_THRESHOLD: float = 0.04  # catch pauses at normal system volumes
    SYS_VAD_MIN_SILENCE_MS: int = 300  # catch brief inter-speaker gaps (~14 chunks)
    SYS_VAD_MIN_BUFFER_SECONDS: float = 2.0
    SYS_VAD_MAX_BUFFER_SECONDS: int = 10  # hard drain — must beat mic to echo filter
    SYS_VAD_MIN_SPEECH_RATIO: float = (
        0.05  # reject silence-only buffers (e.g. paused audio)
    )

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
    SUMMARIZER_MLX_KV_BITS: float | None = None  # TurboQuant KV cache quantization bits

    # Diarization (pyannote-audio, optional)
    DIARIZATION_MODEL: str = "pyannote/speaker-diarization-3.1"
    DIARIZATION_DEVICE: str = "mps"  # MPS default; CPU fallback on failure


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
DIARIZATION_MODEL = config.DIARIZATION_MODEL
DIARIZATION_DEVICE = config.DIARIZATION_DEVICE
