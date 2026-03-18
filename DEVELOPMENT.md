# Scarecrow Developer Setup

This document covers developer bootstrap before the product's own
`scarecrow setup` wizard exists.

## Goals

- Get a clean MacBook ready for Scarecrow development
- Standardize the local validation command
- Document temporary manual steps that exist before first-run automation is built

## Baseline Tools

Install these first:

- Xcode Command Line Tools
- Homebrew
- Rust via `rustup`
- Python 3
- `sqlite3`
- `ffmpeg`
- `opus-tools`
- `ripgrep`
- `cmake`
- `llama.cpp`

Suggested commands:

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
xcode-select --install
brew install rustup-init python sqlite ffmpeg opus-tools ripgrep cmake llama.cpp
rustup-init
source ~/.cargo/env
rustup component add rustfmt clippy
python3 --version
rustc --version
cargo --version
sqlite3 --version
ffmpeg -version
opusenc --version
rg --version
llama-cli --version || true
```

## Optional Tools For Full-Feature Validation

- BlackHole 2ch for system audio capture
- Audio MIDI Setup for Multi-Output Device configuration
- Hugging Face account plus `huggingface-cli` for pyannote access

## Manual Audio Setup Before `scarecrow setup`

Install the package if you want to validate dual-channel capture:

```bash
brew install blackhole-2ch
```

Then:

1. Install BlackHole 2ch.
2. Open Audio MIDI Setup and create a Multi-Output Device.
3. Set the physical output device as the clock source.
4. Enable drift correction on BlackHole, not on the clock-source hardware.
5. Keep all relevant devices on the same sample rate, preferably 48 kHz.
6. Confirm system audio reaches BlackHole before testing Scarecrow capture.

## Manual Worker/Auth Setup Before `scarecrow setup`

If preparing for future worker-side validation before the wizard exists:

```bash
mkdir -p ~/.local/share/scarecrow
python3 -m venv ~/.local/share/scarecrow/venv
~/.local/share/scarecrow/venv/bin/python -m pip install --upgrade pip jiwer "huggingface_hub[cli]"
~/.local/share/scarecrow/venv/bin/huggingface-cli login
```

This mirrors the default runtime location for the worker environment
(`$SCARECROW_DATA/venv/` when `data_dir` is left at its default value).
Then accept the model terms for the required pyannote models in the browser.
The actual `pyannote-audio` / PyTorch worker runtime is not scaffolded in this
repo yet; this step only prepares shared auth and WER tooling ahead of the
worker milestone.

## GGUF Provisioning

Scarecrow expects GGUF files to exist locally for cleanup, summary, and query
tasks. Default search locations are:

- `~/Models`
- `~/.cache/llama.cpp`

Recommended starting layout:

```text
~/Models/
├── qwen2.5-3b-instruct-q4_k_m.gguf
└── qwen2.5-7b-instruct-q4_k_m.gguf
```

Recommended backend choice:

- use `llama-server` when you want a persistent warm local API and lower
  repeated query latency
- use `llama-cli` for simpler one-shot subprocess execution and smaller
  orchestration surface

The setup wizard should validate that the selected backend can invoke the
chosen model successfully before Scarecrow treats it as healthy.

## Silero VAD Model

The daemon uses Silero VAD for voice activity detection. The ONNX model file
is not bundled with the `ort` crate and must be downloaded manually before the
VAD integration milestone (M4):

```bash
mkdir -p ~/.local/share/scarecrow/models
curl -L -o ~/.local/share/scarecrow/models/silero_vad.onnx \
  https://github.com/snakers4/silero-vad/raw/master/files/silero_vad.onnx
```

The model is small (~2 MB) and does not require authentication. Once
`scarecrow setup` exists, it will handle this download automatically.

## Local Model Direction

Use `llama.cpp` as the default local runtime for:

- transcript cleanup and normalization
- rolling summaries
- query answering

Integration rule:

- Do not invoke local models through `Claude Code`, `cclocal`, or any other
  agent wrapper from Scarecrow itself.
- Use `cclocal` only for manual prompt iteration and evaluation during
  development.
- The product runtime should invoke `llama.cpp` directly, either through
  `llama-server` for a persistent local API or `llama-cli` for one-shot worker
  subprocess calls.

## Model Selection Policy

Scarecrow should not guess blindly and should not depend on shell aliases.

Selection order:

1. Explicit config in `scarecrow.toml`
2. Discovered compatible local GGUF models
3. Graceful degradation with a clear error or disabled feature

Scarecrow should maintain a local model catalog with:

- file path
- size
- quantization
- last modified time
- intended use tags: `cleanup`, `summary`, `query`
- validation status: `untested`, `ok`, `failed`

Recommended persisted catalog path:

- `~/.local/share/scarecrow/state/model_catalog.json`

Example catalog entry:

```json
{
  "path": "/Users/dave/Models/qwen2.5-7b-instruct-q4_k_m.gguf",
  "size_bytes": 4680000000,
  "quantization": "Q4_K_M",
  "last_modified": "2026-03-18T14:30:00Z",
  "intended_use": ["summary", "query"],
  "validation_status": "ok"
}
```

Recommended auto-discovery config shape:

```toml
[llm]
model_dirs = ["~/Models", "~/.cache/llama.cpp"]
cleanup_model = ""
summary_model = ""
query_model = ""
backend = "llama-server"          # or "llama-cli"
```

Optional explicit pinning can override discovery per role:

```toml
[llm]
cleanup_model = "qwen2.5-3b-instruct-q4_k_m.gguf"
summary_model = "qwen2.5-7b-instruct-q4_k_m.gguf"
query_model = "qwen2.5-7b-instruct-q4_k_m.gguf"
```

Fallback heuristics apply whenever a role is unset or the selected model is
missing, unhealthy, or incompatible:

- prefer smaller instruct models for cleanup
- prefer larger instruct models with more context for summary and query work
- skip models that exceed the current RAM budget
- validate a candidate with a short smoke test before marking it healthy
- if multiple healthy candidates exist for the same role, choose deterministically
  by explicit role match, then context suitability, then smaller resource cost

Hot-path live captions remain a speech-to-text problem, not a text-generation
problem. Keep their implementation on a dedicated STT path. Apple Foundation
Models are not the live-caption engine for Scarecrow; if an Apple-native path
is explored later, it would be via Apple's speech stack instead.

## Validation

Use the repo-level validation entrypoint:

```bash
./scripts/validate.sh
```

This command should fail closed when the current phase gate is not met. Missing
workspace scaffolding or required local tools is a validation failure, not a
skip.

Expected evolution:

- P0/P1: bootstrap, workspace, and basic repo validation
- P2/P3: audio, IPC, and hot-path smoke checks
- P4/P5: worker, query, and integration checks
- P6/P7: retention, setup, soak, and failure-mode checks

Every milestone should add its checks to this command rather than creating
isolated ad hoc validation workflows.

Validator maturity expectations:

- P0/P1: tool presence, docs present, scaffolding present, config/schema smoke
  checks
- P2/P3: add audio/TUI/IPC smoke checks to `./scripts/validate.sh`
- P4/P5: add worker, model-selection, and query smoke checks
- P6/P7: add setup, retention, and integration/hardening checks
