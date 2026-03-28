#!/usr/bin/env python3
"""Interactive setup for Scarecrow — shows parakeet config info and alias setup."""

from pathlib import Path


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
    print("Scarecrow uses parakeet-mlx for VAD-based batch transcription.")
    print("Audio drains at natural speech pauses (600ms+ silence),")
    print("with a 30-second hard max for continuous speech.")
    print()
    print("Backend: parakeet-mlx (Apple Silicon GPU)")
    print("Model:   mlx-community/parakeet-tdt-0.6b-v3")
    print()


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
    setup_alias()

    print("You're all set! Run `sc` to start Scarecrow.")
    print()


if __name__ == "__main__":
    main()
