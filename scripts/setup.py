#!/usr/bin/env python3
"""Interactive setup for Scarecrow — walks through model selection."""

import re
from pathlib import Path

# Models available for each role, ordered by size
MODELS = [
    ("tiny.en", "~75 MB", "Fastest, least accurate — good for live preview"),
    ("base.en", "~140 MB", "Fast, slightly better accuracy"),
    ("small.en", "~460 MB", "Good balance of speed and accuracy"),
    ("medium.en", "~1.5 GB", "High accuracy, slower — good for final transcript"),
    ("large-v3", "~3 GB", "Best accuracy, multilingual, slowest"),
]

MODEL_NAMES = [m[0] for m in MODELS]

DEFAULTS = {
    "live": "base.en",
    "batch": "medium.en",
}


def print_header():
    print()
    print("=" * 60)
    print("  Scarecrow Setup")
    print("=" * 60)
    print()


def explain_two_model():
    print("HOW SCARECROW USES TWO MODELS")
    print("-" * 40)
    print()
    print("Scarecrow runs two Whisper models simultaneously:")
    print()
    print("  1. LIVE model (always running)")
    print("     Shows real-time captions as you speak.")
    print("     Needs to be fast, so accuracy is secondary.")
    print(f"     Default: {DEFAULTS['live']}")
    print()
    print("  2. BATCH model (runs every 30 seconds)")
    print("     Produces the final, accurate transcript.")
    print("     Can be slower since it runs in the background.")
    print(f"     Default: {DEFAULTS['batch']}")
    print()
    print("A smaller live model + larger batch model gives you")
    print("instant feedback AND an accurate transcript.")
    print()


def print_models():
    print("AVAILABLE MODELS")
    print("-" * 40)
    for i, (name, size, desc) in enumerate(MODELS, 1):
        default_tag = ""
        if name == DEFAULTS["live"]:
            default_tag = " [default live]"
        elif name == DEFAULTS["batch"]:
            default_tag = " [default batch]"
        print(f"  {i}. {name:<12} {size:<10} {desc}{default_tag}")
    print()


def choose_model(role: str, default: str) -> str:
    """Prompt user to pick a model for a given role."""
    default_idx = MODEL_NAMES.index(default) + 1
    while True:
        choice = input(f"  {role} model [{default_idx}]: ").strip()
        if not choice:
            return default
        try:
            idx = int(choice)
            if 1 <= idx <= len(MODELS):
                return MODEL_NAMES[idx - 1]
        except ValueError:
            # Allow typing model name directly
            if choice in MODEL_NAMES:
                return choice
        print(f"    Please enter 1-{len(MODELS)} or a model name.")


def check_cached(model_name: str) -> bool:
    """Check if a model is already downloaded."""
    cache_dir = Path.home() / ".cache" / "huggingface" / "hub"
    return (cache_dir / f"models--Systran--faster-whisper-{model_name}").exists()


def write_config(live_model: str, batch_model: str):
    """Update config.py with selected models."""
    config_path = Path(__file__).resolve().parent.parent / "scarecrow" / "config.py"
    text = config_path.read_text()
    text = re.sub(
        r'^(REALTIME_MODEL\s*=\s*")[^"]*(")',
        rf"\g<1>{live_model}\g<2>",
        text,
        flags=re.MULTILINE,
    )
    text = re.sub(
        r'^(FINAL_MODEL\s*=\s*")[^"]*(")',
        rf"\g<1>{batch_model}\g<2>",
        text,
        flags=re.MULTILINE,
    )
    config_path.write_text(text)


def setup_alias():
    """Show alias setup instructions."""
    project_dir = Path(__file__).resolve().parent.parent
    print("SHELL ALIAS")
    print("-" * 40)
    print()
    print("Add this to your ~/.zshrc (or ~/.bashrc):")
    print()
    print(f'  alias sc="uv run --project {project_dir} scarecrow"')
    print()
    print("Then reload your shell or run: source ~/.zshrc")
    print()


def main():
    print_header()
    explain_two_model()
    print_models()

    print("MODEL SELECTION")
    print("-" * 40)
    print("  Enter a number (1-5) or press Enter for the default.")
    print()

    live_model = choose_model("Live", DEFAULTS["live"])
    batch_model = choose_model("Batch", DEFAULTS["batch"])

    print()
    print(f"  Live model:  {live_model}")
    print(f"  Batch model: {batch_model}")

    # Check cache status
    print()
    for label, model in [("Live", live_model), ("Batch", batch_model)]:
        cached = check_cached(model)
        status = "cached" if cached else "will download on first run"
        print(f"  {label} ({model}): {status}")

    # Write config if models changed
    if live_model != DEFAULTS["live"] or batch_model != DEFAULTS["batch"]:
        print()
        confirm = input("  Write to config.py? [Y/n]: ").strip().lower()
        if confirm in ("", "y", "yes"):
            write_config(live_model, batch_model)
            print("  Config updated.")
        else:
            print("  Skipped — config unchanged.")
    else:
        print()
        print("  Using defaults — no config changes needed.")

    print()
    setup_alias()

    print("You're all set! Run `sc` to start Scarecrow.")
    print()


if __name__ == "__main__":
    main()
