#!/bin/zsh
# Launcher for Scarecrow TUI — works around Python 3.12.13 .pth regression
# Use this in the iTerm2 Scarecrow profile instead of calling .venv/bin/scarecrow directly
cd /Users/dave/Documents/Projects/scarecrow
exec .venv/bin/python -m scarecrow "$@"
