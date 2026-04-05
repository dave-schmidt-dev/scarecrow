# Scarecrow - Claude Code Instructions

## Agent roles
Use Sonnet subagents for implementation tasks. Opus reviews their output for quality, lint, tests, and architectural alignment.

## Before committing
Wait for the user to manually test changes that affect observable behavior (audio quality, UI rendering, hardware interaction). Only commit after they confirm it works.

## Roadmap
Roadmap and future work changes go in `TODO.md`. Do not store roadmap items in memory files.

## Development commands
See the **Development** section in `README.md` for test runner, package management, build, and lint commands.
