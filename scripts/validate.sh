#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

echo "Scarecrow validation"

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

echo "- docs present"

if ! rg -q "\[llm\]" SPEC.md tasks.md; then
  echo "missing [llm] config coverage in planning docs" >&2
  exit 1
fi

if ! rg -q "llama.cpp" README.md SPEC.md tasks.md DEVELOPMENT.md; then
  echo "missing llama.cpp runtime references in planning docs" >&2
  exit 1
fi

echo "- planning doc consistency checks passed"

if [[ -f "Cargo.toml" ]]; then
  echo "- running cargo fmt --check"
  cargo fmt --check
  echo "- running cargo clippy"
  cargo clippy --all-targets --all-features -- -D warnings
  echo "- running cargo test"
  cargo test --all-targets --all-features
else
  echo "- Cargo workspace not scaffolded yet; skipping Rust validation"
fi

if [[ -f "pyproject.toml" || -f "requirements.txt" ]]; then
  echo "- Python project files detected; extend scripts/validate.sh with worker checks"
else
  echo "- Worker project not scaffolded yet; skipping Python validation"
fi

echo "validation completed"
