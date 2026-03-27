#!/usr/bin/env python3
"""Interactive setup for Scarecrow — walks through batch model selection."""

import re
from pathlib import Path

# Models available for batch transcription, ordered by size
MODELS = [
    ("tiny.en", "~75 MB", "Fastest, least accurate"),
    ("base.en", "~140 MB", "Fast, slightly better accuracy"),
    ("small.en", "~460 MB", "Good balance of speed and accuracy"),
    ("medium.en", "~1.5 GB", "High accuracy, slower"),
    ("large-v3-turbo", "~1.6 GB", "Near large-v3 accuracy, much faster — recommended"),
    ("large-v3", "~3 GB", "Best accuracy, multilingual, slowest"),
]

MODEL_NAMES = [m[0] for m in MODELS]

DEFAULT_BATCH = "large-v3-turbo"


def print_header():
    print()
    print("=" * 60)
    print("  Scarecrow Setup")
    print("=" * 60)
    print()


def explain_architecture():
    print("HOW SCARECROW WORKS")
    print("-" * 40)
    print()
    print("Scarecrow uses Whisper for batch transcription every 15 seconds.")
    print("You choose the model below.")
    print(f"Default: {DEFAULT_BATCH}")
    print()


def print_models():
    print("AVAILABLE BATCH MODELS")
    print("-" * 40)
    for i, (name, size, desc) in enumerate(MODELS, 1):
        default_tag = " [default]" if name == DEFAULT_BATCH else ""
        print(f"  {i}. {name:<12} {size:<10} {desc}{default_tag}")
    print()


def choose_model(default: str) -> str:
    """Prompt user to pick a batch model."""
    default_idx = MODEL_NAMES.index(default) + 1
    while True:
        choice = input(f"  Batch model [{default_idx}]: ").strip()
        if not choice:
            return default
        try:
            idx = int(choice)
            if 1 <= idx <= len(MODELS):
                return MODEL_NAMES[idx - 1]
        except ValueError:
            if choice in MODEL_NAMES:
                return choice
        print(f"    Please enter 1-{len(MODELS)} or a model name.")


def check_cached(model_name: str) -> bool:
    """Check if a model is already downloaded."""
    cache_dir = Path.home() / ".cache" / "huggingface" / "hub"
    return (cache_dir / f"models--Systran--faster-whisper-{model_name}").exists()


def write_config(batch_model: str):
    """Update config.py with selected batch model."""
    config_path = Path(__file__).resolve().parent.parent / "scarecrow" / "config.py"
    text = config_path.read_text()
    text = re.sub(
        r'^(FINAL_MODEL\s*=\s*")[^"]*(")',
        lambda match: f"{match.group(1)}{batch_model}{match.group(2)}",
        text,
        flags=re.MULTILINE,
    )
    config_path.write_text(text)


def write_backend(backend: str):
    """Update config.py with selected backend."""
    config_path = Path(__file__).resolve().parent.parent / "scarecrow" / "config.py"
    text = config_path.read_text()
    text = re.sub(
        r'^(BACKEND\s*=\s*")[^"]*(")',
        lambda match: f"{match.group(1)}{backend}{match.group(2)}",
        text,
        flags=re.MULTILINE,
    )
    config_path.write_text(text)


def choose_backend() -> str:
    """Prompt user to choose a transcription backend."""
    print("BACKEND SELECTION")
    print("-" * 40)
    print("  1. whisper    faster-whisper (CPU, any platform) [default]")
    print("  2. parakeet   parakeet-mlx (Apple Silicon GPU, macOS 13/15+)")
    print()
    while True:
        choice = input("  Backend [1]: ").strip()
        if not choice or choice == "1":
            return "whisper"
        if choice == "2":
            return "parakeet"
        if choice in ("whisper", "parakeet"):
            return choice
        print("    Please enter 1 or 2.")


def setup_alias():
    """Show alias setup instructions."""
    project_dir = Path(__file__).resolve().parent.parent
    print("SHELL ALIAS")
    print("-" * 40)
    print()
    print("Add this to your ~/.zshrc (or ~/.bashrc):")
    print()
    print(f'  alias sc="{project_dir}/bin/scarecrow"')
    print()
    print("Then reload your shell or run: source ~/.zshrc")
    print()


def main():
    print_header()
    explain_architecture()

    backend = choose_backend()
    print()

    if backend == "parakeet":
        print("  Backend: parakeet-mlx (Apple Silicon GPU)")
        print("  Model:   mlx-community/parakeet-tdt-0.6b-v3")
        print("  Batch interval: 5s")
        print()
        write_backend(backend)
        print("  Config updated.")
    else:
        write_backend(backend)
        print_models()

        print("MODEL SELECTION")
        print("-" * 40)
        print("  Enter a number (1-5) or press Enter for the default.")
        print()

        batch_model = choose_model(DEFAULT_BATCH)

        print()
        print(f"  Batch model: {batch_model}")

        cached = check_cached(batch_model)
        status = "cached" if cached else "will download on first run"
        print(f"  Batch ({batch_model}): {status}")

        if batch_model != DEFAULT_BATCH:
            print()
            confirm = input("  Write to config.py? [Y/n]: ").strip().lower()
            if confirm in ("", "y", "yes"):
                write_config(batch_model)
                print("  Config updated.")
            else:
                print("  Skipped — config unchanged.")
        else:
            print()
            print("  Using default — no config changes needed.")

    print()
    setup_alias()

    print("You're all set! Run `sc` to start Scarecrow.")
    print()


if __name__ == "__main__":
    main()
