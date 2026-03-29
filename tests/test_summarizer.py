"""Tests for the session summarizer."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from scarecrow import config
from scarecrow.summarizer import (
    _build_prompt,
    _compute_ctx_size,
    _discover_gguf,
    _estimate_tokens,
    _generate,
    _model_name_from_gguf,
    _write_error_summary,
    summarize_session,
)

_real_gguf = _discover_gguf()
_integration = pytest.mark.integration
_requires_model = pytest.mark.skipif(
    _real_gguf is None,
    reason="Nemotron GGUF not found in HuggingFace cache",
)

# ---------------------------------------------------------------------------
# GGUF Discovery
# ---------------------------------------------------------------------------


def test_discover_gguf_finds_model(tmp_path: Path) -> None:
    hub_root = tmp_path / ".cache" / "huggingface" / "hub"
    model_dir = hub_root / "models--foo--Nemotron-Nano-bar-GGUF"
    snapshot_dir = model_dir / "snapshots" / "abc123"
    snapshot_dir.mkdir(parents=True)
    gguf_file = snapshot_dir / "model.gguf"
    gguf_file.touch()

    with patch("scarecrow.summarizer.Path.home", return_value=tmp_path):
        result = _discover_gguf()

    assert result == gguf_file


def test_discover_gguf_skips_mmproj(tmp_path: Path) -> None:
    hub_root = tmp_path / ".cache" / "huggingface" / "hub"
    model_dir = hub_root / "models--foo--Nemotron-Nano-bar-GGUF"
    snapshot_dir = model_dir / "snapshots" / "abc123"
    snapshot_dir.mkdir(parents=True)
    (snapshot_dir / "mmproj-vision.gguf").touch()

    with patch("scarecrow.summarizer.Path.home", return_value=tmp_path):
        result = _discover_gguf()

    assert result is None


def test_discover_gguf_skips_download_in_progress(tmp_path: Path) -> None:
    hub_root = tmp_path / ".cache" / "huggingface" / "hub"
    model_dir = hub_root / "models--foo--Nemotron-Nano-bar-GGUF"
    snapshot_dir = model_dir / "snapshots" / "abc123"
    snapshot_dir.mkdir(parents=True)
    (snapshot_dir / "file.gguf.downloadInProgress").touch()

    with patch("scarecrow.summarizer.Path.home", return_value=tmp_path):
        result = _discover_gguf()

    assert result is None


def test_discover_gguf_returns_none_empty_cache(tmp_path: Path) -> None:
    with patch("scarecrow.summarizer.Path.home", return_value=tmp_path):
        result = _discover_gguf()

    assert result is None


# ---------------------------------------------------------------------------
# Context Sizing
# ---------------------------------------------------------------------------


def test_estimate_tokens() -> None:
    text = "hello world"
    assert _estimate_tokens(text) == len(text) // config.SUMMARIZER_CHARS_PER_TOKEN


def test_compute_ctx_size_minimum() -> None:
    result = _compute_ctx_size(1)
    assert result == config.SUMMARIZER_MIN_CTX


def test_compute_ctx_size_scales_up() -> None:
    result = _compute_ctx_size(150000)
    expected_needed = 150000 + 500 + config.SUMMARIZER_OUTPUT_BUDGET
    assert result >= expected_needed
    assert result > config.SUMMARIZER_MIN_CTX


def test_compute_ctx_size_capped() -> None:
    result = _compute_ctx_size(600000)
    assert result == 524288  # 512K hard cap


# ---------------------------------------------------------------------------
# Prompt Construction
# ---------------------------------------------------------------------------


def _make_event(event_type: str, **kwargs) -> dict:
    return {"type": event_type, **kwargs}


def test_build_prompt_transcript_text() -> None:
    events = [_make_event("transcript", text="Hello from transcript")]
    _, user_content = _build_prompt(events)
    assert "Hello from transcript" in user_content


def test_build_prompt_context_in_system_only() -> None:
    events = [_make_event("note", tag="CONTEXT", text="Speaker is named Alice")]
    system_prompt, user_content = _build_prompt(events)
    assert "Speaker is named Alice" in system_prompt
    assert "Speaker is named Alice" not in user_content


def test_build_prompt_notes_inline() -> None:
    events = [_make_event("note", tag="NOTE", text="Important observation")]
    _, user_content = _build_prompt(events)
    assert "[NOTE: Important observation]" in user_content


def test_build_prompt_tasks_inline() -> None:
    events = [_make_event("note", tag="TASK", text="Follow up with team")]
    _, user_content = _build_prompt(events)
    assert "[TASK: Follow up with team]" in user_content


def test_build_prompt_divider_adds_blank_line() -> None:
    events = [
        _make_event("transcript", text="Before divider"),
        _make_event("divider"),
        _make_event("transcript", text="After divider"),
    ]
    _, user_content = _build_prompt(events)
    assert "\n\n" in user_content


def test_build_prompt_pause_resume() -> None:
    events = [
        _make_event("pause"),
        _make_event("resume"),
    ]
    _, user_content = _build_prompt(events)
    assert "[Recording paused]" in user_content
    assert "[Recording resumed]" in user_content


def test_build_prompt_session_name_as_context() -> None:
    events = [
        _make_event("session_renamed", name="Weekly standup", slug="weekly-standup"),
        _make_event("transcript", text="Let's get started."),
    ]
    system_prompt, user_content = _build_prompt(events)
    assert "Weekly standup" in system_prompt
    assert "Weekly standup" not in user_content


# ---------------------------------------------------------------------------
# In-Process Generation (mocked)
# ---------------------------------------------------------------------------


def test_generate_loads_model_and_returns_text(tmp_path: Path) -> None:
    gguf = tmp_path / "model.gguf"
    gguf.touch()

    mock_llm = MagicMock()
    mock_llm.create_chat_completion.return_value = {
        "choices": [{"message": {"content": "Generated summary"}}],
        "usage": {"total_tokens": 300},
    }

    with patch("llama_cpp.Llama", return_value=mock_llm) as mock_cls:
        text, tokens = _generate(gguf, "system prompt", "user content", 8192)

    assert text == "Generated summary"
    assert tokens == 300
    mock_cls.assert_called_once_with(
        model_path=str(gguf),
        n_ctx=8192,
        n_gpu_layers=-1,
        flash_attn=True,
        verbose=False,
    )
    mock_llm.create_chat_completion.assert_called_once()
    call_kw = mock_llm.create_chat_completion.call_args[1]
    messages = call_kw["messages"]
    assert any(m["role"] == "system" for m in messages)
    assert any(m["role"] == "user" for m in messages)


def test_generate_cleans_up_on_error(tmp_path: Path) -> None:
    gguf = tmp_path / "model.gguf"
    gguf.touch()

    mock_llm = MagicMock()
    mock_llm.create_chat_completion.side_effect = RuntimeError("OOM")

    with (
        patch("llama_cpp.Llama", return_value=mock_llm),
        pytest.raises(RuntimeError, match="OOM"),
    ):
        _generate(gguf, "system", "user", 8192)

    # Model should still be cleaned up (del llm runs in finally)


# ---------------------------------------------------------------------------
# Error Summary
# ---------------------------------------------------------------------------


def test_write_error_summary_content(tmp_path: Path) -> None:
    path = _write_error_summary(tmp_path, "something went wrong")
    content = path.read_text(encoding="utf-8")
    assert "something went wrong" in content
    assert "resummarize.py" in content


def test_write_error_summary_contains_session_path(tmp_path: Path) -> None:
    path = _write_error_summary(tmp_path, "error")
    content = path.read_text(encoding="utf-8")
    assert str(tmp_path) in content


# ---------------------------------------------------------------------------
# End-to-End (mocked)
# ---------------------------------------------------------------------------


def _write_transcript(session_dir: Path, events: list[dict]) -> Path:
    transcript = session_dir / "transcript.jsonl"
    lines = [json.dumps(e) for e in events]
    transcript.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return transcript


def test_summarize_session_success(tmp_path: Path) -> None:
    events = [
        {"type": "session_start", "timestamp": "2026-01-01T10:00:00"},
        {"type": "transcript", "text": "Hello, this is the meeting."},
        {"type": "transcript", "text": "We discussed Q4 goals."},
    ]
    _write_transcript(tmp_path, events)

    # Realistic GGUF path so _model_name_from_gguf extracts the name
    gguf_dir = tmp_path / "models--unsloth--Nemotron-3-Nano-30B-A3B-GGUF"
    gguf_dir.mkdir(parents=True)
    fake_gguf = gguf_dir / "snapshots" / "abc" / "model.gguf"
    fake_gguf.parent.mkdir(parents=True)
    fake_gguf.touch()

    with (
        patch("scarecrow.summarizer._discover_gguf", return_value=fake_gguf),
        patch(
            "scarecrow.summarizer._generate",
            return_value=("Summary text here", 500),
        ),
    ):
        result = summarize_session(tmp_path)

    assert result is not None
    assert result == tmp_path / "summary.md"
    content = result.read_text(encoding="utf-8")
    assert "Summary text here" in content
    assert "Generated by Scarecrow" in content
    assert "Nemotron-3-Nano-30B-A3B" in content
    assert "words transcribed" in content
    assert "summarized in" in content
    assert "500 tokens used" in content


def test_summarize_session_no_transcript(tmp_path: Path) -> None:
    result = summarize_session(tmp_path)

    assert result is not None
    content = result.read_text(encoding="utf-8")
    assert "failed" in content.lower()
    assert "transcript" in content.lower()


def test_summarize_session_no_gguf(tmp_path: Path) -> None:
    events = [
        {"type": "transcript", "text": "Some speech here."},
    ]
    _write_transcript(tmp_path, events)

    with patch("scarecrow.summarizer._discover_gguf", return_value=None):
        result = summarize_session(tmp_path)

    assert result is not None
    content = result.read_text(encoding="utf-8")
    assert "failed" in content.lower()
    assert "download" in content.lower()


def test_summarize_session_generate_error(tmp_path: Path) -> None:
    """summarize_session writes an error summary when _generate raises."""
    events = [{"type": "transcript", "text": "Some speech here."}]
    _write_transcript(tmp_path, events)

    fake_gguf = tmp_path / "model.gguf"
    fake_gguf.touch()

    with (
        patch("scarecrow.summarizer._discover_gguf", return_value=fake_gguf),
        patch(
            "scarecrow.summarizer._generate",
            side_effect=RuntimeError("Metal OOM"),
        ),
    ):
        result = summarize_session(tmp_path)

    assert result is not None
    content = result.read_text(encoding="utf-8")
    assert "failed" in content.lower()


def test_summarize_session_empty_speech(tmp_path: Path) -> None:
    """Sessions with only metadata events produce an error summary."""
    events = [
        {"type": "session_start", "timestamp": "2026-01-01T10:00:00"},
        {"type": "session_end", "timestamp": "2026-01-01T10:01:00"},
    ]
    _write_transcript(tmp_path, events)

    result = summarize_session(tmp_path)

    assert result is not None
    content = result.read_text(encoding="utf-8")
    assert "No transcribed speech" in content


# ---------------------------------------------------------------------------
# Model name extraction
# ---------------------------------------------------------------------------


def test_model_name_from_gguf_extracts_repo() -> None:
    path = Path(
        "/cache/models--unsloth--Nemotron-3-Nano-30B-A3B-GGUF/snapshots/abc/model.gguf"
    )
    assert _model_name_from_gguf(path) == "Nemotron-3-Nano-30B-A3B"


def test_model_name_from_gguf_fallback() -> None:
    path = Path("/some/random/path/mymodel.gguf")
    assert _model_name_from_gguf(path) == "mymodel"


# ---------------------------------------------------------------------------
# Integration tests (require real GGUF model on disk)
# ---------------------------------------------------------------------------


@_integration
@_requires_model
def test_generate_returns_text_from_real_model() -> None:
    """_generate loads the real GGUF and produces a non-empty response."""
    assert _real_gguf is not None
    text, tokens = _generate(
        _real_gguf,
        system_prompt="Reply with exactly: OK",
        user_content="Say OK.",
        ctx_size=2048,
    )
    assert isinstance(text, str)
    assert len(text) > 0
    assert isinstance(tokens, int)
    assert tokens > 0


@_integration
@_requires_model
def test_generate_respects_system_prompt() -> None:
    """The model follows the system prompt instruction."""
    assert _real_gguf is not None
    text, _ = _generate(
        _real_gguf,
        system_prompt=(
            "You are a test helper. Respond with ONLY the single "
            "word 'PINEAPPLE'. No other text."
        ),
        user_content="Go.",
        ctx_size=2048,
    )
    assert "PINEAPPLE" in text.upper()


@_integration
@_requires_model
def test_summarize_session_end_to_end(tmp_path: Path) -> None:
    """Full pipeline: transcript on disk -> summary.md with real model."""
    events = [
        {"type": "session_start", "timestamp": "2026-01-01T10:00:00"},
        {
            "type": "transcript",
            "text": (
                "Today we decided to migrate the database from "
                "PostgreSQL to SQLite for the embedded use case."
            ),
        },
        {
            "type": "transcript",
            "text": "Alice will handle the schema conversion by Friday.",
        },
        {"type": "note", "tag": "TASK", "text": "Convert schema to SQLite"},
        {"type": "session_end", "timestamp": "2026-01-01T10:05:00"},
    ]
    _write_transcript(tmp_path, events)

    result = summarize_session(tmp_path)

    assert result is not None
    assert result.name == "summary.md"
    content = result.read_text(encoding="utf-8")
    # Must contain actual summary content, not an error
    assert "failed" not in content.lower()
    # Footer with stats
    assert "Generated by Scarecrow" in content
    assert "words transcribed" in content
    assert "tokens used" in content
