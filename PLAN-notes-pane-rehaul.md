# Scarecrow UI Rehaul: Live Pane → Notes Pane

**Date:** 2026-03-27
**Status:** Complete (2026-03-27)

## Summary

Kill the live pane and Apple Speech entirely. Replace with a notes input pane for manual annotations. Drop batch interval from 30s to 15s. Move pause/quit to modifier keys. Single source of truth: Whisper batch transcription.

## Phase Dependency Graph

```
Phase 1 (Remove live pane) ──┐
                              ├──▶ Phase 3 (Notes UI) ──▶ Phase 5 (Note submission logic)
Phase 2 (Batch interval)  ───┘         │
                                       └──▶ Phase 4 (Keybindings) ──▶ Phase 6 (Cleanup)
```

Phases 1+2 run in parallel. Then 3+4 in parallel. Then 5. Then 6.

---

## Phase 1: Remove Live Pane & Apple Speech

**Goal:** Strip LiveCaptioner, the live pane widget, and all associated state. App launches with only transcript pane + footer.

### Tasks

| # | Task | Files |
|---|------|-------|
| 1.1 | Remove LiveCaptioner from `app.py` — imports, constructor param, instance vars (`_live_stable`, `_live_partial`), all live pane methods (`_render_live`, `_set_live_partial`, `_append_live`, `_update_live`, `_update_live_message`, `_update_live_partial`), public API (`update_live_preview`, `append_caption`), captioner callbacks, `_tick_captioner`, captioner timer, captioner code in `_start_recording`/`action_pause`/`action_quit`/`cleanup_after_exit` | `app.py` |
| 1.2 | Remove live pane styles (`#live-pane`, `#live-content`) | `app.tcss` |
| 1.3 | Delete `live_captioner.py` entirely | `live_captioner.py` |
| 1.4 | Remove LiveCaptioner from `__main__.py` — import, construction, `prepare()` block, constructor arg, startup print line | `__main__.py` |
| 1.5 | Remove `LIVE_HISTORY_LIMIT` | `config.py` |
| 1.6 | Remove live captioner refs from vulture whitelist | `vulture_whitelist.py` |
| 1.7 | Remove/update tests — delete `test_live_captioner.py`, remove `_mock_captioner()` helpers, remove all live-pane-specific tests from `test_app.py`, `test_behavioral.py`, `test_regressions.py`. Rewrite `test_start_recording_unwinds_recorder_when_transcriber_begin_fails` to use a different failure trigger. | `tests/test_live_captioner.py` (delete), `tests/test_app.py`, `tests/test_behavioral.py`, `tests/test_regressions.py`, `scripts/run_test_suite.sh` |
| 1.8 | Delete manual test helpers | `scripts/test_live_captioner.py` (delete), `scripts/test_apple_speech.py` (delete) |

### Testing Gate
- [ ] `scripts/run_test_suite.sh` passes
- [ ] `ruff check scarecrow/ tests/` clean
- [ ] `vulture scarecrow/ vulture_whitelist.py` clean — no dead code from Apple Speech / live pane survives
- [ ] No imports of `Speech`, `AVFoundation`, `Foundation`, `NSRunLoop` remain in active code
- [ ] App launches showing only info bar + transcript pane + footer

---

## Phase 2: Drop Batch Interval to 15s

**Goal:** `BATCH_INTERVAL_SECONDS` → 15. Update all references.

### Tasks

| # | Task | Files |
|---|------|-------|
| 2.1 | Change `BATCH_INTERVAL_SECONDS = 30` → `15` | `app.py` |
| 2.2 | Update hardcoded "30s" / "30 seconds" in startup output | `__main__.py` |
| 2.3 | Update README references | `README.md` |

### Testing Gate
- [ ] All tests pass (they import the constant symbolically)
- [ ] InfoBar countdown shows 15

---

## Phase 3: Notes Pane UI

**Goal:** Add notes input area where the live pane was.

### Tasks

| # | Task | Files |
|---|------|-------|
| 3.1 | Add to `compose()`: a pane label, shortcut hints line (`F1: [ACTION]  F2: [FOLLOW-UP]  F3: [NOTE]`), and an `Input` widget (`#note-input`) | `app.py` |
| 3.2 | Add styles for `#note-shortcuts` and `#note-input` | `app.tcss` |
| 3.3 | Verify transcript pane (`#captions`, already `height: 1fr`) fills remaining space | `app.tcss` |

### Testing Gate
- [ ] App launches with notes pane visible below transcript
- [ ] Input accepts focus and text entry
- [ ] Shortcut hints visible
- [ ] Visual layout correct

---

## Phase 4: Keybinding Changes

**Goal:** Move pause/quit to modifier keys so they don't conflict with text input.

### Tasks

| # | Task | Files |
|---|------|-------|
| 4.1 | Change bindings: `p` → `ctrl+p`, `q` → `ctrl+q` | `app.py` |
| 4.2 | Update tests that simulate `press("p")` / `press("q")` | `test_app.py`, `test_behavioral.py` |
| 4.3 | Update README keybindings section | `README.md` |

### Testing Gate
- [ ] `ctrl+p` pauses/resumes, `ctrl+q` quits
- [ ] Typing `p` and `q` in the Input widget inserts text, not commands
- [ ] All keybinding tests pass

### Risk
`ctrl+q` may be intercepted by terminal XON/XOFF. Test in iTerm2. Fallback: `ctrl+x` for quit.

---

## Phase 5: Note Submission Logic

**Goal:** Wire up note entry to both RichLog and transcript file.

### Tasks

| # | Task | Files |
|---|------|-------|
| 5.1 | Add `_submit_note(tag: str \| None)` — reads input, builds `[TAG] HH:MM:SS -- text`, writes to RichLog with styled markup, writes to transcript file via session, increments word count, clears input | `app.py` |
| 5.2 | Add bindings: `F1` → `action_note_action` (`[ACTION]`), `F2` → `action_note_followup` (`[FOLLOW-UP]`), `F3` → `action_note_quick` (`[NOTE]`) | `app.py` |
| 5.3 | Add `on_input_submitted` handler — Enter key submits with `[NOTE]` tag | `app.py` |
| 5.4 | Add tests: submission writes to RichLog, writes to file, includes tag, includes wall-clock timestamp, clears input, increments word count, empty is no-op, F1/F2/F3 tags, notes work during IDLE (no session = UI only) | `test_behavioral.py` |

### Testing Gate
- [ ] All three tag types appear in RichLog and transcript file
- [ ] Timestamps are wall-clock (`datetime.now()`), not session elapsed
- [ ] Empty submissions are no-ops
- [ ] Notes work in RECORDING, PAUSED, and IDLE states

---

## Phase 6: Cleanup & Polish

**Goal:** Final dead code sweep, docs, consistency.

### Tasks

| # | Task | Files |
|---|------|-------|
| 6.1 | Final vulture scan — confirm zero dead code from live pane / Apple Speech | all |
| 6.2 | Grep for orphaned references: `live_captioner`, `LiveCaptioner`, `live-pane`, `live-content`, `Apple Speech`, `SFSpeech`, `_live_stable`, `_live_partial` — must return zero hits in active code | all |
| 6.3 | Update README: remove two-engine architecture, update TUI layout, update keybindings, update file list | `README.md` |
| 6.4 | Close `BUG-20260325-live-pane-scroll-resets-at-boundary` as removed | `BUGS.md` |
| 6.5 | Add rehaul entry to HISTORY | `HISTORY.md` |
| 6.6 | Full validation: test suite, ruff, vulture, manual smoke test | all |

### Final Gate
- [ ] Full test suite green
- [ ] Lint clean
- [ ] Vulture clean
- [ ] Grep for dead references returns nothing
- [ ] Manual smoke test: launch → record → type notes with F1/F2/F3/Enter → pause with ctrl+p → quit with ctrl+q

---

## Files Summary

**Delete:** `live_captioner.py`, `tests/test_live_captioner.py`, `scripts/test_live_captioner.py`, `scripts/test_apple_speech.py`

**Heavy edits:** `app.py`, `test_behavioral.py`, `test_app.py`, `README.md`

**Light edits:** `app.tcss`, `__main__.py`, `config.py`, `vulture_whitelist.py`, `run_test_suite.sh`, `test_regressions.py`, `BUGS.md`, `HISTORY.md`

**Create:** Nothing — all new code lives in existing files.

---

## Post-Rehaul Refinements (2026-03-27)

These changes were made after the initial rehaul was declared complete:

- **Prefix commands replaced F1/F2/F3:** Tags are now selected by typing a prefix — `/action` or `/a` for `[ACTION]`, `/followup` or `/f` for `[FOLLOW-UP]`, plain text for `[NOTE]`. F1/F2/F3 bindings removed entirely. README and HISTORY updated.
- **Timestamp fix:** Transcript dividers now show the start of the audio batch window (`_batch_window_start`) rather than the time Whisper finishes processing. Dividers are now accurate to when the audio was captured.
- **500ms audio overlap between batch chunks:** Each batch window retains the last 500ms of audio from the previous window to reduce word drops at chunk boundaries.
- **`_batch_window_start` tracking:** New instance variable records the wall-clock time each batch window opens; consumed by the divider-writing logic.
