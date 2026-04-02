---
description: "Run a report-only external audit with four parallel lanes and save a timestamped report under reports/."
argument-hint: "[optional path or focus]"
---

# External Audit

Run a structured external audit of the current project or an optional target path. This is a review workflow, not a fixing workflow.

## Inputs

The user invoked this prompt with: `$ARGUMENTS`

- If `$ARGUMENTS` begins with a valid path, audit that path.
- Otherwise audit the current working directory.
- If `$ARGUMENTS` includes extra text after the path, treat it as an optional focus and reflect it in the report.

## Hard rules

- This audit is report-only.
- Do not change source code, tests, docs, configs, or task trackers.
- The only permitted mutations are:
  - create `<project>/reports/` if missing
  - if the target is inside a git repo, ensure `reports/` is ignored in `.gitignore`
  - write the final report file
- Do not suppress or hand-wave failed checks.
- If a lane cannot complete, say exactly why.
- Missing tests for active behavior are a major finding.
- Full functionality review means attempting every discoverable validation path that is reasonable in the current environment.
- Actively look for AI-agent bloat:
  - unnecessary docs or duplicated docs
  - stale plans, drafts, or generated artifacts
  - giant low-signal files
  - verbose comments that do not justify their cost
  - excessive or low-value test sprawl
  - speculative abstractions and dead helpers

## Workflow

1. Resolve the audit root.
   - If `$ARGUMENTS` starts with an existing path, use it.
   - Else if the current directory is inside a git repo, use `git rev-parse --show-toplevel`.
   - Else use the current working directory.
2. Prepare the output location.
   - Create `<root>/reports/` if missing.
   - If `<root>` is inside a git repo, ensure `.gitignore` contains `reports/` or `/reports/`.
   - Create `reports/external-audit-YYYY-MM-DD_HH-MM-SS.md`.
3. Do immediate local setup work before delegating.
   - Identify the repo root, git status, top-level structure, primary languages and frameworks, package managers, CI files, hook files, and declared validation commands from README, Makefiles, package manifests, scripts, and CI config.
   - Record environment constraints needed for a fair audit.
4. Launch four parallel audit lanes and keep them report-only.
   - Lane 1: documentation and policy
   - Lane 2: tests and functionality
   - Lane 3: security
   - Lane 4: general sanity and architecture
5. Run reasonable local validation commands.
   - Use the project’s declared commands first.
   - If nothing is declared, run the most likely safe checks for the detected stack.
   - Capture both passes and failures.
6. Consolidate lane findings into a single severity-ranked report.
7. Write the report file and return its path plus a concise summary in chat.

## Lane 1: Documentation and policy

Perform a documentation and repo-policy audit.

Check:
- whether README, setup docs, development docs, and task-tracking docs match the repo's current behavior
- whether documented commands actually exist and are likely to work
- whether required project docs are missing, stale, contradictory, or bloated
- whether changelog, bug log, or task tracker discipline appears to be followed
- whether there are stale agent instructions, obsolete plans, or duplicated policy files

Return:
- severity-ranked findings with file references
- what was inspected
- any contradictions or stale docs
- missing docs and low-value docs separately

## Lane 2: Testing and functionality

Perform a practical functionality and test audit.

Check:
- what the project claims to do
- whether tests cover active behavior and critical paths
- whether regression tests exist for bug-prone areas
- whether dead or low-value tests appear to outnumber useful tests
- what validations can actually be run in the current environment
- whether observed behavior likely matches the documented behavior

Return:
- severity-ranked findings with file references
- commands attempted and their outcomes
- gaps in test coverage
- places where functionality could not be verified

## Lane 3: Security

Perform a practical security and secrets-hygiene review.

Check:
- secret leakage risks in tracked files, logs, fixtures, examples, and configs
- unsafe shelling out, path handling, temp file use, deserialization, eval-like behavior, and injection surfaces
- auth, authorization, session, and privilege boundaries where relevant
- insecure defaults, debug paths, or overly permissive settings
- dependency and supply-chain risk signals the repo exposes locally
- whether ignore rules and logging practices are likely to leak sensitive data

Return:
- severity-ranked findings with file references
- what was inspected
- blocked checks or things that require live infrastructure to judge fully
- concrete security gaps, not generic advice

## Lane 4: General sanity and architecture

Audit the codebase shape and maintenance posture.

Check:
- giant files, tangled modules, dead scripts, stale experiments, and duplicate logic
- unnecessary abstraction, framework churn, or over-engineering
- AI-agent bloat across code, tests, comments, plans, and docs
- low-signal repo clutter that makes review or maintenance harder
- mismatches between project complexity and the amount of scaffolding around it
- areas where simplicity or sharper boundaries would materially improve sanity

Return:
- severity-ranked findings with file references
- concrete bloat indicators
- architecture or maintainability risks
- what seems appropriately scoped versus overbuilt

## Report

Write the report to the timestamped file under `reports/`.

Use this structure:

```md
# External Audit

- Timestamp:
- Audit root:
- Git repo:
- Commit:
- Dirty worktree:
- Optional focus:

## Executive Summary

- Overall verdict:
- Critical findings:
- High findings:
- Validation coverage:
- Blocked areas:

## Findings by Severity

### Critical
- [Lane] Finding summary
  - Evidence:
  - Impact:
  - Recommended next action:

### High
- ...

### Medium
- ...

### Low
- ...

## Lane Results

### Documentation and Policy
- Checks performed:
- Findings:
- Gaps or unknowns:

### Testing and Functionality
- Checks performed:
- Findings:
- Gaps or unknowns:

### Security
- Checks performed:
- Findings:
- Gaps or unknowns:

### General Sanity and Architecture
- Checks performed:
- Findings:
- Gaps or unknowns:

## Validation Matrix
- Declared check:
- Source of truth:
- Command attempted:
- Result:
- Notes:

## AI-Agent Bloat Signals
- ...
```
