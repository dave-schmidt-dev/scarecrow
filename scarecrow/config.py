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

    # VAD-based chunking — tuned via LibriSpeech WER sweep (bench_librispeech.py):
    #   750ms silence: optimal for both 0.6B and 1.1B models (plateau at 750ms,
    #     no improvement beyond). Reduces mid-sentence splits vs 600ms.
    #   30s max buffer: hard drain rarely triggers with 750ms silence; provides
    #     headroom for sustained speech without pauses.
    VAD_SILENCE_THRESHOLD: float = 0.01  # RMS below this counts as silence
    VAD_MIN_SILENCE_MS: int = 750  # consecutive silence before triggering drain
    VAD_MAX_BUFFER_SECONDS: int = 30  # hard drain if no silence found by this point
    VAD_POLL_INTERVAL_MS: int = 150  # how often to check for silence
    # Minimum fraction of chunks with speech before sending to Parakeet.
    # TODO: benchmark via bench_librispeech.py
    VAD_MIN_SPEECH_RATIO: float = 0.15

    # System audio VAD — tuned for clean digital audio (no ambient noise)
    SYS_VAD_SILENCE_THRESHOLD: float = 0.003  # lower than mic (no noise floor)
    SYS_VAD_MIN_SILENCE_MS: int = 1500  # longer than mic — reduce mid-sentence cuts
    SYS_VAD_MIN_SPEECH_RATIO: float = 0.0  # disabled — no ambient noise to filter

    # System audio capture device
    SYSTEM_AUDIO_DEVICE: str = "BlackHole"  # substring match, case-insensitive

    # Writer thread queue size (bounded to prevent unbounded memory growth)
    # ~12.5 seconds of audio at 48kHz with 1024-sample blocks
    WRITER_QUEUE_SIZE: int = 600

    # Minimum seconds between transcript dividers
    DIVIDER_INTERVAL: int = 60

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

    # Summarizer (local LLM via llama-cpp-python, in-process)
    SUMMARIZER_MODEL_PATTERN: str = "*Nemotron*Nano*GGUF"
    SUMMARIZER_OUTPUT_BUDGET: int = 4096  # max tokens for summary output
    SUMMARIZER_MIN_CTX: int = 131072  # 128K floor — Mamba-2, no memory cost
    SUMMARIZER_CHARS_PER_TOKEN: int = 4  # rough heuristic for token estimation


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
VAD_SILENCE_THRESHOLD = config.VAD_SILENCE_THRESHOLD
VAD_MIN_SILENCE_MS = config.VAD_MIN_SILENCE_MS
VAD_MAX_BUFFER_SECONDS = config.VAD_MAX_BUFFER_SECONDS
VAD_POLL_INTERVAL_MS = config.VAD_POLL_INTERVAL_MS
VAD_MIN_SPEECH_RATIO = config.VAD_MIN_SPEECH_RATIO
SYS_VAD_SILENCE_THRESHOLD = config.SYS_VAD_SILENCE_THRESHOLD
SYS_VAD_MIN_SILENCE_MS = config.SYS_VAD_MIN_SILENCE_MS
SYS_VAD_MIN_SPEECH_RATIO = config.SYS_VAD_MIN_SPEECH_RATIO
SYSTEM_AUDIO_DEVICE = config.SYSTEM_AUDIO_DEVICE
WRITER_QUEUE_SIZE = config.WRITER_QUEUE_SIZE
DIVIDER_INTERVAL = config.DIVIDER_INTERVAL
DEFAULT_RECORDINGS_DIR = config.DEFAULT_RECORDINGS_DIR
SUMMARIZER_MODEL_PATTERN = config.SUMMARIZER_MODEL_PATTERN
SUMMARIZER_OUTPUT_BUDGET = config.SUMMARIZER_OUTPUT_BUDGET
SUMMARIZER_MIN_CTX = config.SUMMARIZER_MIN_CTX
SUMMARIZER_CHARS_PER_TOKEN = config.SUMMARIZER_CHARS_PER_TOKEN
OBSIDIAN_VAULT_DIR = config.OBSIDIAN_VAULT_DIR
