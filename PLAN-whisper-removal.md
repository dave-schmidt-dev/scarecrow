# Whisper Removal Migration Spec

## Scope

Remove the `faster-whisper` backend from Scarecrow, leaving only `parakeet-mlx`. Touches 7 source files, 6 test files, 2 scripts, 1 benchmark, and 4 docs files.

---

## Pre-Merge Gate

**Condition:** Merge `feature/parakeet-mlx` into `main` before any removal work.

```bash
git checkout main && git merge feature/parakeet-mlx
bash scripts/run_test_suite.sh    # all tests pass
git tag v0.2.0-pre-cleanup        # rollback reference
git push && git push --tags
```

---

## Phase 1 — Remove Whisper Dependency

**Goal:** Drop `faster-whisper`, promote `parakeet-mlx` to required.

**Changes — `pyproject.toml`:**
- Remove `"faster-whisper>=0.9,<2.0"` from `dependencies`
- Move `"parakeet-mlx>=0.1"` from `[project.optional-dependencies]` to `dependencies`
- Delete empty `[project.optional-dependencies]` section

**Risk:** Low

**Gate 1:**
```bash
uv sync
python -c "from parakeet_mlx import from_pretrained"   # must succeed
python -c "import faster_whisper" 2>&1 | grep Error     # must ImportError
```

---

## Phase 2 + 8 — Purge `runtime.py` + `__main__.py` (same commit)

**Goal:** Remove all whisper model management and startup display branches.

**Changes — `scarecrow/runtime.py`:**
- Remove `from faster_whisper import WhisperModel`
- Remove `model_cache_path()` function
- Remove `warm_tqdm_lock()` function
- Remove `self._batch_model` from `ModelManager.__init__()`
- Remove `get_batch_model()` method
- Remove `_create_model()` static method
- Update `release_models()` — remove `self._batch_model = None`
- Update `_prepare_unlocked()` — remove `warm_tqdm_lock()` call
- Update class docstring

**Changes — `scarecrow/__main__.py`:**
- Remove `_model_cache_path()` wrapper
- Remove `import model_cache_path` from runtime
- Replace `if backend == "parakeet" ... else ...` display block with unconditional parakeet output
- Replace conditional loading message with `"Loading Parakeet model..."`

**Risk:** Medium — `model_cache_path` referenced in both files + tests

**Gate 2:**
```bash
python -c "from scarecrow.runtime import ModelManager"
python -c "from scarecrow import __main__"
```

---

## Phase 3 — Simplify `config.py`

**Goal:** Remove whisper-only constants.

**Remove:**
- `FINAL_MODEL`, `LANGUAGE`, `BEAM_SIZE`, `CONDITION_ON_PREVIOUS_TEXT`
- `BACKEND` constant and comment
- `BATCH_INTERVAL_WHISPER`

**Update:**
- Comment: `# 16kHz — required by parakeet-mlx`

**Keep (rename later):** `BATCH_INTERVAL_PARAKEET`

**Risk:** Low — `AttributeError` makes breakage loud

**Gate 3:**
```bash
python -c "from scarecrow import config; config.PARAKEET_MODEL"  # OK
python -c "from scarecrow import config; config.FINAL_MODEL"     # must AttributeError
```

---

## Phase 4 + 5 — Simplify `transcriber.py` + `app.py` (same commit)

**Goal:** Remove whisper transcription path, context injection, backend branching.

**Changes — `transcriber.py`:**
- Remove `_transcribe_whisper()` method
- Remove `initial_prompt` parameter from `transcribe_batch()`
- Remove `if config.BACKEND` dispatch — direct call to `_transcribe_parakeet()`
- Remove `self._batch_lock` (whisper-only serialization)
- Simplify `preload_batch_model()` — just `get_parakeet_model()`
- Update module docstring

**Changes — `app.py`:**

*A. Module-level:*
- Delete `_get_batch_interval()` — use `config.BATCH_INTERVAL_PARAKEET` directly
- Remove `BATCH_INTERVAL_SECONDS` or assign directly

*B. InfoBar:*
- Remove `"buf " if ... else "batch "` — always `"buf "`

*C. `__init__`:*
- Remove `_context_entries`, `_awaiting_context`, `_previous_batch_tail`

*D. `compose()`:*
- Remove all `if config.BACKEND == "parakeet"` branches — use parakeet values directly

*E. `on_mount()`:*
- Replace context-start auto-start with direct `_start_recording()` after preflight

*F. `_show_help()`:*
- Remove `/context` and `/clear` from help text

*G. `_submit_batch_transcription()` + `_flush_final_batch()`:*
- Remove `initial_prompt` kwarg from `transcribe_batch()` calls

*H. Delete methods:*
- `_build_initial_prompt()`
- `_update_tail()`
- `_handle_context_start()`
- `_handle_add_context()`
- `_handle_clear_context()`

*I. Simplify `_append_transcript()`:*
- Remove `_update_tail()` call — becomes just `_record_transcript()`

*J. `_start_recording()`:*
- `overlap_ms=200` (no conditional)
- `self._use_vad = True` (no conditional)
- Remove whisper batch timer path

*K. `on_input_submitted()`:*
- Remove `_awaiting_context` branch
- Remove `/clear` and `/context` routing

*L. `_update_context_display()`:*
- Remove `"CONTEXT"` from counts — keep TASK and NOTE only

**Risk:** HIGH — largest change, core app logic

**Gate 4+5:**
```bash
uv run ruff check scarecrow/
python -c "from scarecrow.app import ScarecrowApp"
uv run pytest tests/test_parakeet_backend.py -x
```

---

## Phase 6 — Update and Delete Tests

**Delete entirely:**
- `test_transcriber.py`: 8 whisper-patched tests, `test_prepare_sets_is_ready` (rewrite), `test_get_batch_model_thread_safety` (replace with parakeet version)
- `test_behavioral.py`: entire "Context injection" section (~12 tests), `_build_initial_prompt` tests, `_update_tail` test
- `test_app.py`: `test_app_launches_in_idle_no_autostart`
- `test_regressions.py`: 3 `model_cache_path` tests, rewrite `test_batch_passes_audio`
- `test_parakeet_backend.py`: whisper branch of `test_batch_interval_reflects_backend`
- `test_startup.py`: rewrite `_batch_model_is_cached` for parakeet, rewrite mocked whisper test
- `test_integration.py`: rewrite `_batch_model_cached` for parakeet, update real audio test
- `test_setup.py`: delete or rewrite for simplified setup script

**Rewrite (not delete):**
- Transcriber tests: replace whisper mocks with parakeet mocks
- Integration test: route through parakeet
- Startup test: no WhisperModel patch needed

**Risk:** Medium — must not reduce coverage

**Gate 6:**
```bash
bash scripts/run_test_suite.sh    # full suite, all pass
uv run ruff check scarecrow/ tests/
uv run vulture scarecrow/ vulture_whitelist.py
```

---

## Phase 7 — Simplify Scripts and Benchmark

**Changes — `scripts/setup.py`:**
- Remove model selection, backend selection, `write_config()`, `write_backend()`
- Simplify to: print parakeet config info + `setup_alias()`
- Or delete entirely if trivial

**Changes — `benchmarks/bench_librispeech.py`:**
- Remove `make_whisper()`, `transcribe_whisper()`
- Remove `--backend whisper` and `"both"` args
- Remove `print_comparison()` (single backend now)
- Keep VAD and fixed chunking modes

**Risk:** Low

**Gate 7:**
```bash
python benchmarks/bench_librispeech.py --help
```

---

## Phase 9 — Documentation

**`README.md`:**
- Remove "Backends" dual-bullet, replace with single parakeet description
- Remove startup context prompt docs (auto-starts now)
- Remove `/context`, `/clear` command docs
- Delete "Context injection" section
- Update architecture paragraph and file descriptions

**`TODO.md`:**
- Remove whisper comparison items and resource usage

**`BUGS.md`:**
- Annotate whisper-era bugs as "superseded by whisper removal" (do not delete)

**`HISTORY.md`:**
- Add migration entry

**Risk:** Low

---

## Phase 10 — Final Lint and Smoke Test

```bash
uv run ruff check scarecrow/ tests/ --fix
uv run vulture scarecrow/ vulture_whitelist.py
bash scripts/run_test_suite.sh
# Manual: launch app, speak, confirm transcript, quit
```

---

## Post-Migration Cleanup

1. Rename `BATCH_INTERVAL_PARAKEET` → `BATCH_INTERVAL` in config + all references
2. Delete `scripts/setup.py` if trivial
3. Remove `tqdm` if no longer a transitive dep
4. Tag `v0.3.0-parakeet-only`

---

## Risk Summary

| Phase | Risk | Notes |
|-------|------|-------|
| Pre-merge | Low | Run tests before removal |
| 1. pyproject | Low | `uv sync` immediately |
| 2+8. runtime + main | Medium | Same commit, both ref `model_cache_path` |
| 3. config | Low | AttributeError is loud |
| 4+5. transcriber + app | **HIGH** | Largest change, core logic |
| 6. tests | Medium | Write replacements before deleting |
| 7. scripts/bench | Low | Dev tools only |
| 9. docs | Low | No code impact |
| 10. lint/smoke | Low | Final safety net |

**Recommended commit grouping:**
- Commit A: Phases 1 + 2 + 8 (dependency + runtime + main)
- Commit B: Phases 3 + 4 + 5 (config + transcriber + app)
- Commit C: Phase 6 (tests)
- Commit D: Phases 7 + 9 + 10 (scripts + docs + cleanup)
