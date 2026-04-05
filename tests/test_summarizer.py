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
    _create_backend,
    _discover_gguf,
    _estimate_tokens,
    _extract_segment_events,
    _fmt_duration,
    _generate,
    _GgufBackend,
    _MlxBackend,
    _model_name_from_gguf,
    _strip_reasoning,
    _write_error_summary,
    summarize_session,
    summarize_session_segments,
)

_real_gguf = _discover_gguf()
_integration = pytest.mark.integration
_requires_model = pytest.mark.skipif(
    _real_gguf is None,
    reason="GGUF model not found in HuggingFace cache",
)

# ---------------------------------------------------------------------------
# GGUF Discovery
# ---------------------------------------------------------------------------


def test_discover_gguf_finds_model(tmp_path: Path) -> None:
    hub_root = tmp_path / ".cache" / "huggingface" / "hub"
    model_dir = hub_root / "models--unsloth--gemma-4-27b-it-GGUF"
    snapshot_dir = model_dir / "snapshots" / "abc123"
    snapshot_dir.mkdir(parents=True)
    gguf_file = snapshot_dir / "model.gguf"
    gguf_file.touch()

    with patch("scarecrow.summarizer.Path.home", return_value=tmp_path):
        result = _discover_gguf()

    assert result == gguf_file


def test_discover_gguf_skips_mmproj(tmp_path: Path) -> None:
    hub_root = tmp_path / ".cache" / "huggingface" / "hub"
    model_dir = hub_root / "models--unsloth--gemma-4-27b-it-GGUF"
    snapshot_dir = model_dir / "snapshots" / "abc123"
    snapshot_dir.mkdir(parents=True)
    (snapshot_dir / "mmproj-vision.gguf").touch()

    with patch("scarecrow.summarizer.Path.home", return_value=tmp_path):
        result = _discover_gguf()

    assert result is None


def test_discover_gguf_skips_download_in_progress(tmp_path: Path) -> None:
    hub_root = tmp_path / ".cache" / "huggingface" / "hub"
    model_dir = hub_root / "models--unsloth--gemma-4-27b-it-GGUF"
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


def test_compute_ctx_size_small_input() -> None:
    result = _compute_ctx_size(1)
    expected_needed = 1 + 500 + config.SUMMARIZER_OUTPUT_BUDGET
    # Should be ceil-aligned to 1024
    assert result >= expected_needed
    assert result % 1024 == 0


def test_compute_ctx_size_scales_up() -> None:
    result = _compute_ctx_size(150000)
    expected_needed = 150000 + 500 + config.SUMMARIZER_OUTPUT_BUDGET
    assert result >= expected_needed


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
    _, user_content, _ = _build_prompt(events)
    assert "Hello from transcript" in user_content


def test_build_prompt_context_in_system_only() -> None:
    events = [_make_event("note", tag="CONTEXT", text="Speaker is named Alice")]
    system_prompt, user_content, _ = _build_prompt(events)
    assert "Speaker is named Alice" in system_prompt
    assert "Speaker is named Alice" not in user_content


def test_build_prompt_notes_inline() -> None:
    events = [_make_event("note", tag="NOTE", text="Important observation")]
    _, user_content, _ = _build_prompt(events)
    assert "[NOTE: Important observation]" in user_content


def test_build_prompt_tasks_inline() -> None:
    events = [_make_event("note", tag="TASK", text="Follow up with team")]
    _, user_content, _ = _build_prompt(events)
    assert "[TASK: Follow up with team]" in user_content


def test_build_prompt_divider_adds_blank_line() -> None:
    events = [
        _make_event("transcript", text="Before divider"),
        _make_event("divider"),
        _make_event("transcript", text="After divider"),
    ]
    _, user_content, _ = _build_prompt(events)
    assert "\n\n" in user_content


def test_build_prompt_pause_resume() -> None:
    events = [
        _make_event("pause"),
        _make_event("resume"),
    ]
    _, user_content, _ = _build_prompt(events)
    assert "[Recording paused]" in user_content
    assert "[Recording resumed]" in user_content


def test_build_prompt_mute_unmute_events() -> None:
    events = [
        {"type": "mute", "source": "mic", "elapsed": 10},
        {"type": "unmute", "source": "mic", "elapsed": 20},
        {"type": "mute", "source": "sys", "elapsed": 30},
        {"type": "unmute", "source": "sys", "elapsed": 40},
    ]
    _, user_content, _ = _build_prompt(events)
    assert "[Mic muted]" in user_content
    assert "[Mic unmuted]" in user_content
    assert "[Sys audio muted]" in user_content
    assert "[Sys audio unmuted]" in user_content


def test_build_prompt_session_name_as_context() -> None:
    events = [
        _make_event("session_renamed", name="Weekly standup", slug="weekly-standup"),
        _make_event("transcript", text="Let's get started."),
    ]
    system_prompt, user_content, _ = _build_prompt(events)
    assert "Weekly standup" in system_prompt
    assert "Weekly standup" not in user_content


# ---------------------------------------------------------------------------
# Reasoning Stripping
# ---------------------------------------------------------------------------


def test_strip_reasoning_removes_think_tags() -> None:
    text = "<think>Let me analyze this...</think>\n## Summary\nHello world"
    assert _strip_reasoning(text) == "## Summary\nHello world"


def test_strip_reasoning_keeps_heading_after_freeform_reasoning() -> None:
    text = "We need to think about this.\nOkay so...\n## Summary\nResult"
    assert _strip_reasoning(text) == "## Summary\nResult"


def test_strip_reasoning_returns_empty_when_no_headings() -> None:
    text = "Just a bunch of reasoning with no structure at all."
    result = _strip_reasoning(text)
    assert result == ""


def test_strip_reasoning_preserves_clean_output() -> None:
    text = "## Summary\nGood summary\n\n## Key Points\n- Point 1"
    assert _strip_reasoning(text) == text


# ---------------------------------------------------------------------------
# In-Process Generation (mocked)
# ---------------------------------------------------------------------------


def test_generate_loads_model_and_returns_text(tmp_path: Path) -> None:
    gguf = tmp_path / "model.gguf"
    gguf.touch()

    mock_llm = MagicMock()
    mock_llm.create_chat_completion.return_value = {
        "choices": [{"message": {"content": "## Summary\nGenerated summary"}}],
        "usage": {"total_tokens": 300},
    }

    with patch("llama_cpp.Llama", return_value=mock_llm) as mock_cls:
        text, tokens = _generate(gguf, "system prompt", "user content", 8192)

    assert text == "## Summary\nGenerated summary"
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
    gguf_dir = tmp_path / "models--unsloth--gemma-4-27b-it-GGUF"
    gguf_dir.mkdir(parents=True)
    fake_gguf = gguf_dir / "snapshots" / "abc" / "model.gguf"
    fake_gguf.parent.mkdir(parents=True)
    fake_gguf.touch()

    with (
        patch("scarecrow.summarizer._discover_gguf", return_value=fake_gguf),
        patch("scarecrow.summarizer._load_model", return_value=MagicMock()),
        patch(
            "scarecrow.summarizer._generate",
            return_value=("Summary text here", 500),
        ),
    ):
        result = summarize_session(tmp_path, backend="gguf")

    assert result is not None
    assert result == tmp_path / "summary.md"
    content = result.read_text(encoding="utf-8")
    assert "Summary text here" in content
    assert "Generated by Scarecrow" in content
    assert "gemma-4-27b-it" in content
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
        result = summarize_session(tmp_path, backend="gguf")

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
        result = summarize_session(tmp_path, backend="gguf")

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
    path = Path("/cache/models--unsloth--gemma-4-27b-it-GGUF/snapshots/abc/model.gguf")
    assert _model_name_from_gguf(path) == "gemma-4-27b-it"


def test_model_name_from_gguf_fallback() -> None:
    path = Path("/some/random/path/mymodel.gguf")
    assert _model_name_from_gguf(path) == "mymodel"


# ---------------------------------------------------------------------------
# Segment extraction
# ---------------------------------------------------------------------------


def test_extract_segment_events_single() -> None:
    """Single segment (no boundaries) returns all events in one list."""
    events = [
        {"type": "transcript", "text": "hello"},
        {"type": "transcript", "text": "world"},
    ]
    result = _extract_segment_events(events, 1)
    assert len(result) == 1
    assert len(result[0]) == 2


def test_extract_segment_events_two_segments() -> None:
    """Events split at segment_boundary into two lists."""
    events = [
        {"type": "transcript", "text": "seg1"},
        {"type": "segment_boundary", "segment": 1, "elapsed": 3600},
        {"type": "transcript", "text": "seg2"},
    ]
    result = _extract_segment_events(events, 2)
    assert len(result) == 2
    assert result[0][0]["text"] == "seg1"
    assert result[1][0]["text"] == "seg2"


def test_extract_segment_events_boundary_not_included() -> None:
    """segment_boundary events are not included in any segment's event list."""
    events = [
        {"type": "transcript", "text": "a"},
        {"type": "segment_boundary", "segment": 1},
        {"type": "transcript", "text": "b"},
    ]
    result = _extract_segment_events(events, 2)
    all_types = [e["type"] for seg in result for e in seg]
    assert "segment_boundary" not in all_types


def test_extract_segment_events_pads_missing() -> None:
    """If fewer boundaries than expected, pad with empty lists."""
    events = [{"type": "transcript", "text": "only one"}]
    result = _extract_segment_events(events, 3)
    assert len(result) == 3
    assert len(result[0]) == 1
    assert len(result[1]) == 0
    assert len(result[2]) == 0


# ---------------------------------------------------------------------------
# Multi-segment summarization (mocked)
# ---------------------------------------------------------------------------


def test_summarize_session_segments_single_delegates(tmp_path: Path) -> None:
    """n_segments=1 delegates to summarize_session."""
    mock_ss = MagicMock(return_value=tmp_path / "summary.md")
    with patch("scarecrow.summarizer.summarize_session", mock_ss):
        result = summarize_session_segments(tmp_path, 1)

    mock_ss.assert_called_once_with(tmp_path, obsidian_dir=None, backend=None)
    assert result == tmp_path / "summary.md"


def test_summarize_session_segments_multi(tmp_path: Path) -> None:
    """Multi-segment produces per-segment files and a synthesized overall summary."""
    events = [
        {"type": "transcript", "text": "Segment one content."},
        {"type": "segment_boundary", "segment": 1, "elapsed": 3600},
        {"type": "transcript", "text": "Segment two content."},
    ]
    _write_transcript(tmp_path, events)

    fake_gguf_dir = tmp_path / "models--test--model-GGUF"
    fake_gguf_dir.mkdir(parents=True)
    fake_gguf = fake_gguf_dir / "snapshots" / "abc" / "model.gguf"
    fake_gguf.parent.mkdir(parents=True)
    fake_gguf.touch()

    call_count = 0

    def mock_generate(gguf, sp, uc, ctx, *, llm=None):
        nonlocal call_count
        call_count += 1
        if call_count <= 2:
            return f"## Summary\nSegment {call_count} summary", 200
        # Synthesis pass
        return "## Summary\nOverall synthesized summary", 200

    with (
        patch("scarecrow.summarizer._discover_gguf", return_value=fake_gguf),
        patch("scarecrow.summarizer._generate", side_effect=mock_generate),
        patch("scarecrow.summarizer._load_model", return_value=MagicMock()),
    ):
        result = summarize_session_segments(tmp_path, 2, backend="gguf")

    assert result is not None
    assert result.name == "summary.md"
    content = result.read_text(encoding="utf-8")
    # Overall summary is synthesized, not concatenated segment headers
    assert "Overall synthesized summary" in content
    assert "segments synthesized" in content
    # Per-segment files should exist
    assert (tmp_path / "summary_seg1.md").exists()
    assert (tmp_path / "summary_seg2.md").exists()
    # Synthesis required 3 generate calls (2 segments + 1 synthesis)
    assert call_count == 3


def test_segment_boundary_ignored_in_prompt() -> None:
    """segment_boundary events are ignored when building prompts."""
    events = [
        {"type": "transcript", "text": "hello"},
        {"type": "segment_boundary", "segment": 1, "elapsed": 3600},
        {"type": "transcript", "text": "world"},
    ]
    _, user_content, _ = _build_prompt(events)
    assert "segment_boundary" not in user_content
    assert "hello" in user_content
    assert "world" in user_content


# ---------------------------------------------------------------------------
# _fmt_duration
# ---------------------------------------------------------------------------


def test_fmt_duration_zero() -> None:
    assert _fmt_duration(0) == "0s"


def test_fmt_duration_sub_minute() -> None:
    assert _fmt_duration(45) == "45s"


def test_fmt_duration_exactly_one_minute() -> None:
    assert _fmt_duration(60) == "1 min"


def test_fmt_duration_minutes() -> None:
    assert _fmt_duration(2520) == "42 min"


def test_fmt_duration_hours() -> None:
    assert _fmt_duration(3900) == "1h 5m"


def test_fmt_duration_exactly_one_hour() -> None:
    assert _fmt_duration(3600) == "1h 0m"


# ---------------------------------------------------------------------------
# session_metrics extraction
# ---------------------------------------------------------------------------


def test_build_prompt_extracts_elapsed_from_session_metrics() -> None:
    events = [
        {"type": "session_start", "timestamp": "2026-01-01T10:00:00"},
        {"type": "transcript", "text": "Hello world"},
        {"type": "session_metrics", "elapsed": 2520, "word_count": 2},
        {"type": "session_end", "timestamp": "2026-01-01T10:42:00"},
    ]
    _, user_content, elapsed = _build_prompt(events)
    assert elapsed == 2520
    assert "Hello world" in user_content


def test_build_prompt_elapsed_zero_when_no_session_metrics() -> None:
    events = [
        {"type": "transcript", "text": "Just a transcript"},
    ]
    _, _, elapsed = _build_prompt(events)
    assert elapsed == 0


def test_build_prompt_session_metrics_not_in_user_content() -> None:
    events = [
        {"type": "session_metrics", "elapsed": 300, "word_count": 50},
        {"type": "transcript", "text": "Actual content"},
    ]
    _, user_content, _ = _build_prompt(events)
    assert "session_metrics" not in user_content
    assert "Actual content" in user_content


def test_summarize_session_footer_contains_session_duration(tmp_path: Path) -> None:
    """summary.md footer includes session duration derived from session_metrics."""
    events = [
        {"type": "session_start", "timestamp": "2026-01-01T10:00:00"},
        {"type": "transcript", "text": "Hello, this is the meeting."},
        {"type": "session_metrics", "elapsed": 2520, "word_count": 6},
        {"type": "session_end", "timestamp": "2026-01-01T10:42:00"},
    ]
    _write_transcript(tmp_path, events)

    fake_gguf_dir = tmp_path / "models--unsloth--gemma-4-27b-it-GGUF"
    fake_gguf_dir.mkdir(parents=True)
    fake_gguf = fake_gguf_dir / "snapshots" / "abc" / "model.gguf"
    fake_gguf.parent.mkdir(parents=True)
    fake_gguf.touch()

    with (
        patch("scarecrow.summarizer._discover_gguf", return_value=fake_gguf),
        patch("scarecrow.summarizer._load_model", return_value=MagicMock()),
        patch(
            "scarecrow.summarizer._generate",
            return_value=("## Summary\nMeeting summary", 400),
        ),
    ):
        result = summarize_session(tmp_path, backend="gguf")

    assert result is not None
    content = result.read_text(encoding="utf-8")
    assert "session: 42 min" in content


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


# ---------------------------------------------------------------------------
# Backend factory & MLX backend (mocked)
# ---------------------------------------------------------------------------


def test_create_backend_gguf(tmp_path: Path) -> None:
    """_create_backend('gguf') with a discovered GGUF returns a _GgufBackend."""
    fake_gguf = tmp_path / "model.gguf"
    fake_gguf.touch()

    with patch("scarecrow.summarizer._discover_gguf", return_value=fake_gguf):
        be = _create_backend("gguf", ctx_size=8192)

    assert isinstance(be, _GgufBackend)


def test_create_backend_mlx() -> None:
    """_create_backend('mlx') returns a _MlxBackend instance."""
    be = _create_backend("mlx")
    assert isinstance(be, _MlxBackend)


def test_create_backend_gguf_not_found() -> None:
    """_create_backend('gguf') raises FileNotFoundError when no GGUF exists."""
    with (
        patch("scarecrow.summarizer._discover_gguf", return_value=None),
        pytest.raises(FileNotFoundError),
    ):
        _create_backend("gguf")


def test_mlx_backend_load_unsets_hf_offline(monkeypatch) -> None:
    """_MlxBackend.load() temporarily removes HF_HUB_OFFLINE and restores it."""
    import os

    monkeypatch.setenv("HF_HUB_OFFLINE", "1")

    # Track what HF_HUB_OFFLINE is during the mlx_vlm.load() call
    captured_during_load: list[str | None] = []

    def fake_mlx_load(model_id, **kwargs):
        captured_during_load.append(os.environ.get("HF_HUB_OFFLINE"))
        return MagicMock(), MagicMock()

    mock_mlx_vlm = MagicMock()
    mock_mlx_vlm.load.side_effect = fake_mlx_load

    be = _MlxBackend("test-model-id")

    with patch.dict("sys.modules", {"mlx_vlm": mock_mlx_vlm}):
        be.load()

    # During load, HF_HUB_OFFLINE should have been absent
    assert captured_during_load == [None]
    # After load, HF_HUB_OFFLINE must be restored to its original value
    assert os.environ.get("HF_HUB_OFFLINE") == "1"


def test_mlx_backend_generate() -> None:
    """_MlxBackend.generate() calls apply_chat_template and mlx_vlm.generate."""
    be = _MlxBackend("my-model/gemma-4")
    be._model = MagicMock()
    be._processor = MagicMock()

    mock_result = MagicMock()
    mock_result.text = "## Summary\nGood summary"
    mock_result.total_tokens = 280
    mock_result.prompt_tokens = 200
    mock_result.generation_tokens = 80

    mock_apply = MagicMock(return_value="<formatted prompt>")
    mock_generate = MagicMock(return_value=mock_result)

    mock_mlx_vlm = MagicMock()
    mock_mlx_vlm.generate = mock_generate
    mock_prompt_utils = MagicMock()
    mock_prompt_utils.apply_chat_template = mock_apply

    with patch.dict(
        "sys.modules",
        {
            "mlx_vlm": mock_mlx_vlm,
            "mlx_vlm.prompt_utils": mock_prompt_utils,
        },
    ):
        text, tokens = be.generate("system prompt", "user content")

    assert "Good summary" in text
    assert tokens == 280
    mock_apply.assert_called_once()
    mock_generate.assert_called_once()


def test_summarize_session_mlx_backend(tmp_path: Path) -> None:
    """summarize_session() with backend='mlx' creates and uses _MlxBackend."""
    events = [
        {"type": "session_start", "timestamp": "2026-01-01T10:00:00"},
        {"type": "transcript", "text": "Hello from the MLX backend test."},
    ]
    _write_transcript(tmp_path, events)

    mock_be = MagicMock(spec=_MlxBackend)
    mock_be.name = "gemma-4-31b-it-4bit"
    mock_be.footer_info = "mlx"
    mock_be.generate.return_value = ("## Summary\nMLX summary", 150)

    with patch(
        "scarecrow.summarizer._create_backend", return_value=mock_be
    ) as mock_factory:
        result = summarize_session(tmp_path, backend="mlx")

    call_args = mock_factory.call_args
    assert call_args[0][0] == "mlx"
    assert call_args[1]["model"] is None
    assert call_args[1]["ctx_size"] > 0
    mock_be.load.assert_called_once()
    mock_be.generate.assert_called_once()
    mock_be.close.assert_called_once()

    assert result is not None
    content = result.read_text(encoding="utf-8")
    assert "MLX summary" in content
    assert "Generated by Scarecrow" in content


def test_gguf_backend_footer_info(tmp_path: Path) -> None:
    """_GgufBackend.footer_info returns 'ctx <ctx_size>'."""
    fake_gguf = tmp_path / "model.gguf"
    fake_gguf.touch()
    be = _GgufBackend(fake_gguf, 131072)
    assert be.footer_info == "ctx 131072"


def test_mlx_backend_footer_info_with_kv_bits() -> None:
    """_MlxBackend.footer_info returns 'mlx · kv_bits <n>' when kv_bits is set."""
    be = _MlxBackend("some/model-id", kv_bits=4)
    assert be.footer_info == "mlx · kv_bits 4"


def test_mlx_backend_footer_info_without_kv_bits() -> None:
    """_MlxBackend.footer_info returns 'mlx' when no kv_bits is set."""
    be = _MlxBackend("some/model-id")
    assert be.footer_info == "mlx"


def test_summarize_session_segments_empty_segment_placeholder(tmp_path: Path) -> None:
    """Empty segments get a placeholder file; synthesis uses non-empty segments only."""
    events = [
        {"type": "transcript", "text": "Segment one content."},
        {"type": "segment_boundary", "segment": 1, "elapsed": 3600},
        # Segment 2 is empty — no transcripts between boundaries
        {"type": "segment_boundary", "segment": 2, "elapsed": 7200},
        {"type": "transcript", "text": "Segment three content."},
    ]
    _write_transcript(tmp_path, events)

    fake_gguf_dir = tmp_path / "models--test--model-GGUF"
    fake_gguf_dir.mkdir(parents=True)
    fake_gguf = fake_gguf_dir / "snapshots" / "abc" / "model.gguf"
    fake_gguf.parent.mkdir(parents=True)
    fake_gguf.touch()

    call_count = 0

    def mock_generate(gguf, sp, uc, ctx, *, llm=None):
        nonlocal call_count
        call_count += 1
        if call_count <= 2:
            return f"## Summary\nSegment {call_count} summary", 200
        return "## Summary\nOverall synthesized summary", 200

    with (
        patch("scarecrow.summarizer._discover_gguf", return_value=fake_gguf),
        patch("scarecrow.summarizer._generate", side_effect=mock_generate),
        patch("scarecrow.summarizer._load_model", return_value=MagicMock()),
    ):
        result = summarize_session_segments(tmp_path, 3, backend="gguf")

    assert result is not None
    content = result.read_text(encoding="utf-8")
    # Overall summary is synthesized
    assert "Overall synthesized summary" in content
    assert "segments synthesized" in content
    # Per-segment files for all three
    assert (tmp_path / "summary_seg1.md").exists()
    assert (tmp_path / "summary_seg2.md").exists()
    assert (tmp_path / "summary_seg3.md").exists()
    # Segment 2 placeholder file still written
    seg2_content = (tmp_path / "summary_seg2.md").read_text(encoding="utf-8")
    assert "No speech detected" in seg2_content
    # 2 segment calls + 1 synthesis = 3 (empty segment skipped)
    assert call_count == 3


def test_summarize_session_segments_passes_backend(tmp_path: Path) -> None:
    """n_segments=1 with backend='mlx' delegates with backend kwarg."""
    mock_ss = MagicMock(return_value=tmp_path / "summary.md")
    with patch("scarecrow.summarizer.summarize_session", mock_ss):
        result = summarize_session_segments(tmp_path, 1, backend="mlx")

    mock_ss.assert_called_once_with(tmp_path, obsidian_dir=None, backend="mlx")
    assert result == tmp_path / "summary.md"
