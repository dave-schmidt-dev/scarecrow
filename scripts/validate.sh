#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

if [[ -f "$HOME/.cargo/env" ]]; then
  # shellcheck disable=SC1090
  source "$HOME/.cargo/env"
fi

echo "Scarecrow validation"

require_cmd() {
  local cmd="$1"
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "missing required tool: $cmd" >&2
    exit 1
  fi
}

require_any_cmd() {
  local label="$1"
  shift
  local cmd
  for cmd in "$@"; do
    if command -v "$cmd" >/dev/null 2>&1; then
      return 0
    fi
  done
  echo "missing required tool: $label" >&2
  exit 1
}

require_pattern() {
  local pattern="$1"
  shift
  local path
  for path in "$@"; do
    if command -v rg >/dev/null 2>&1; then
      if ! rg -q "$pattern" "$path"; then
        echo "missing required pattern '$pattern' in $path" >&2
        exit 1
      fi
    else
      if ! grep -Eq "$pattern" "$path"; then
        echo "missing required pattern '$pattern' in $path" >&2
        exit 1
      fi
    fi
  done
}

required_docs=(
  "README.md"
  "SPEC.md"
  "tasks.md"
  "HISTORY.md"
  "DEVELOPMENT.md"
)

for path in "${required_docs[@]}"; do
  if [[ ! -f "$path" ]]; then
    echo "missing required file: $path" >&2
    exit 1
  fi
done

if [[ ! -f "LICENSE" ]]; then
  echo "missing required file: LICENSE" >&2
  exit 1
fi

echo "- docs present"

require_cmd python3
require_cmd sqlite3
require_cmd rg
require_cmd ffmpeg
require_cmd opusenc
require_any_cmd "llama-cli or llama-server" llama-cli llama-server
echo "- baseline bootstrap tools present: python3, sqlite3, rg, ffmpeg, opusenc, llama.cpp"

# cmake is required once whisper-rs enters the dependency tree (M3+).
# Hard-fail if any crate depends on whisper-rs; warn otherwise.
if rg -q 'whisper-rs' Cargo.toml crates/*/Cargo.toml 2>/dev/null; then
  require_cmd cmake
  echo "- cmake present (required by whisper-rs)"
else
  if ! command -v cmake >/dev/null 2>&1; then
    echo "- WARNING: cmake not found — required starting M3 (whisper-rs). Install with: brew install cmake"
  else
    echo "- cmake present (not yet required but available)"
  fi
fi

require_pattern "\\[llm\\]" SPEC.md tasks.md DEVELOPMENT.md
require_pattern "llama\\.cpp" README.md SPEC.md tasks.md DEVELOPMENT.md
require_pattern "silero" SPEC.md tasks.md DEVELOPMENT.md
require_pattern "delete-last" README.md SPEC.md tasks.md
require_pattern "M11:" SPEC.md tasks.md

echo "- planning doc consistency checks passed"

if [[ ! -f "Cargo.toml" ]]; then
  echo "missing Cargo workspace: P0 gate is not met" >&2
  exit 1
fi

for manifest in \
  "crates/scarecrow-shared/Cargo.toml" \
  "crates/scarecrow-daemon/Cargo.toml" \
  "crates/scarecrow/Cargo.toml"
do
  if [[ ! -f "$manifest" ]]; then
    echo "missing required workspace manifest: $manifest" >&2
    exit 1
  fi
done

require_cmd cargo
require_cmd rustc
echo "- Rust toolchain present"

echo "- running cargo fmt --check"
cargo fmt --check
echo "- running cargo build"
cargo build
echo "- running cargo clippy"
cargo clippy --all-targets --all-features -- -D warnings
echo "- running cargo test"
cargo test --all-targets --all-features

if [[ -f "pyproject.toml" || -f "requirements.txt" ]]; then
  echo "- Python project files detected; extend scripts/validate.sh with worker checks"
else
  echo "- Worker project not scaffolded yet; skipping Python validation"
fi

echo "validation completed"
