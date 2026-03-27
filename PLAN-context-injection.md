# Scarecrow: Whisper Context Injection & Pre-recording Context Prompt

**Date:** 2026-03-27
**Status:** Complete (2026-03-27)

## Summary

Three interlocking changes: (1) write a session timestamp header to every transcript file automatically; (2) replace the auto-start boot sequence with a context-collection prompt so the user can seed Whisper's `initial_prompt` before recording begins; (3) maintain and update that context during recording via `/context` and `/clear` commands, feeding the accumulated context plus a rolling tail of the last batch's text into every `transcribe_batch()` call.

## Phase Dependency Graph

```
Phase 1 (Session timestamp header)  ─────────────────────────────────────────┐
                                                                              │
Phase 2 (Defer auto-start / context prompt UI) ──┐                           │
                                                  ├──▶ Phase 4 (Wire prompt  │
Phase 3 (initial_prompt in transcriber)  ─────────┘     into batch tick) ────┴──▶ Phase 5 (Tests) ──▶ Phase 6 (Cleanup)
```

Phases 1, 2, and 3 are independent and can run in parallel. Phase 4 depends on 2 and 3. Phase 5 covers the full test suite. Phase 6 is cleanup and docs.

---

## Phase 1: Session Timestamp Header

**Goal:** Every transcript file starts with `Session: YYYY-MM-DD HH:MM:SS` as its first line, written at session-creation time.

### Tasks

| # | Task | Files |
|---|------|-------|
| 1.1 | Write `Session: {timestamp}` line in `Session.__init__` via `append_sentence()` immediately after directory creation. | `session.py` |
| 1.2 | Add tests: header is first line, matches format. Update existing tests that assert exact file content to account for the header. | `tests/test_session.py` |

### Testing Gate
- [ ] New session header tests pass
- [ ] All existing `test_session.py` tests pass
- [ ] `ruff check` clean

---

## Phase 2: Defer Auto-start & Context Prompt UI

**Goal:** App launches in IDLE with focus in the notes Input. Enter (empty or with text) starts recording, writing a `[CONTEXT]` block if text was provided.

### Tasks

| # | Task | Files |
|---|------|-------|
| 2.1 | Add `_context_entries: list[str]` and `_awaiting_context: bool = True` to `__init__` | `app.py` |
| 2.2 | Remove `self.set_timer(0.1, self._auto_start)` from `on_mount`. Focus `#note-input` instead. | `app.py` |
| 2.3 | Give the Notes pane label `id="notes-label"` so it can be updated at runtime. Initial text: context prompt. | `app.py` |
| 2.4 | Add `Static(id="context-display")` between transcript pane and notes label. Initially hidden (`display: none`). | `app.py`, `app.tcss` |
| 2.5 | Rewrite `on_input_submitted`: if `_awaiting_context`, call `_handle_context_start(value)`. Otherwise route `/context`, `/clear`, or `_submit_note()`. | `app.py` |
| 2.6 | Implement `_handle_context_start(raw)`: strip text, set `_awaiting_context = False`, restore notes label, run preflight + `_start_recording()`, then write `[CONTEXT]` to session and RichLog if text was provided, update context display. | `app.py` |

### Testing Gate
- [ ] App launches in IDLE, no auto-start
- [ ] `#note-input` focused on mount
- [ ] Empty Enter starts recording, no `[CONTEXT]` in file
- [ ] Non-empty Enter starts recording, `[CONTEXT]` in file and RichLog
- [ ] Context display updates
- [ ] `ruff check` clean

---

## Phase 3: `initial_prompt` Parameter in Transcriber

**Goal:** `transcribe_batch()` accepts optional `initial_prompt` and passes it to `model.transcribe()`.

### Tasks

| # | Task | Files |
|---|------|-------|
| 3.1 | Add `initial_prompt: str \| None = None` parameter to `transcribe_batch()` | `transcriber.py` |
| 3.2 | Pass `initial_prompt` to `model.transcribe()` when not None | `transcriber.py` |
| 3.3 | Add tests: prompt passed through, omitted when None | `tests/test_transcriber.py` |

### Testing Gate
- [ ] New transcriber tests pass
- [ ] All existing transcriber tests pass
- [ ] `ruff check` clean

---

## Phase 4: Wire Context Into Batch Tick

**Goal:** Every batch call gets `initial_prompt` from context entries + previous batch tail. `/context` and `/clear` commands work.

### Tasks

| # | Task | Files |
|---|------|-------|
| 4.1 | Add `_previous_batch_tail: str = ""` to `__init__` | `app.py` |
| 4.2 | Add `_build_initial_prompt() -> str \| None` — joins context entries + tail | `app.py` |
| 4.3 | Add `_update_tail(text)` — stores last 35 words of batch output | `app.py` |
| 4.4 | Update `_submit_batch_transcription()` — pass `initial_prompt=self._build_initial_prompt()` | `app.py` |
| 4.5 | Update `_append_transcript()` — call `_update_tail(text)` after recording | `app.py` |
| 4.6 | Update `_flush_final_batch()` — pass `initial_prompt` | `app.py` |
| 4.7 | Handle `/context` command: append to entries, write to RichLog + session, update display | `app.py` |
| 4.8 | Handle `/clear` command: wipe entries + tail, hide display, set status | `app.py` |
| 4.9 | Add `_update_context_display()` helper — show/hide `#context-display` | `app.py` |
| 4.10 | Style `#context-display` in CSS | `app.tcss` |

### Testing Gate
- [ ] `/context` appends entries and shows in display
- [ ] `/clear` wipes entries and hides display
- [ ] `_build_initial_prompt` assembles context + tail correctly
- [ ] Batch submission passes prompt to transcriber
- [ ] `_flush_final_batch` passes prompt
- [ ] `ruff check` clean

---

## Phase 5: Test Suite Updates

**Goal:** Fix all broken tests from auto-start removal, add coverage for context features.

### Tasks

| # | Task | Files |
|---|------|-------|
| 5.1 | Update tests that relied on auto-start: add Enter press to start recording | `tests/test_app.py` |
| 5.2 | Add `test_app_launches_in_idle_no_autostart` | `tests/test_app.py` |
| 5.3 | Update session tests for header line | `tests/test_session.py` |
| 5.4 | Add behavioral tests for full context flow: empty Enter, non-empty Enter, `/context`, `/clear`, prompt assembly, tail update, notes label changes | `tests/test_behavioral.py` |
| 5.5 | Add transcriber tests for `initial_prompt` | `tests/test_transcriber.py` |

### Testing Gate
- [ ] Full test suite green
- [ ] `ruff check` clean
- [ ] `vulture` clean

---

## Phase 6: Cleanup & Polish

### Tasks

| # | Task | Files |
|---|------|-------|
| 6.1 | Update vulture whitelist for new methods | `vulture_whitelist.py` |
| 6.2 | Remove or retain `_auto_start` method (confirm no callers) | `app.py` |
| 6.3 | Update README: context prompt flow, `/context`, `/clear` commands | `README.md` |
| 6.4 | Add entry to HISTORY.md | `HISTORY.md` |
| 6.5 | Final validation: test suite, ruff, vulture | all |

### Final Gate
- [ ] Full test suite green
- [ ] Lint clean
- [ ] Vulture clean
- [ ] Manual smoke test passes

---

## Files Summary

**Delete:** Nothing.

**Heavy edits:** `app.py`, `tests/test_behavioral.py`, `tests/test_app.py`

**Light edits:** `transcriber.py`, `session.py`, `app.tcss`, `tests/test_transcriber.py`, `tests/test_session.py`, `vulture_whitelist.py`, `README.md`, `HISTORY.md`

**Create:** Nothing — all new code lives in existing files.

---

## Risk Areas

1. **Test churn from removing auto-start** — ~8 tests in `test_app.py` implicitly rely on auto-start. All need an explicit Enter press added.
2. **Context-before-session ordering** — `[CONTEXT]` must appear after header but before batch text. Safe because `_start_recording()` is synchronous and first batch tick is 15s away.
3. **`/context` vs note prefix disambiguation** — Must route `/context` and `/clear` before `_submit_note()` in `on_input_submitted`.
4. **Tail length** — 35 words ≈ 50 tokens. Combined with context terms, stays well under Whisper's 224-token limit.
5. **`_flush_final_batch` needs prompt** — Separate call site, easy to forget. Test covers it.
