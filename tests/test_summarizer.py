"""Tests for the session summarizer."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from scarecrow import config
from scarecrow.summarizer import (
    LlamaServer,
    _build_prompt,
    _compute_ctx_size,
    _discover_gguf,
    _estimate_tokens,
    _find_running_server,
    _write_error_summary,
    summarize_session,
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
    # No models dir at all
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
    assert result == config.SUMMARIZER_MIN_CTX  # 32768


def test_compute_ctx_size_scales_up() -> None:
    # 150000 input tokens + 500 overhead + output budget > min ctx (128K)
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
# Server Lifecycle (mocked)
# ---------------------------------------------------------------------------


def test_llama_server_start_calls_popen(tmp_path: Path) -> None:
    gguf = tmp_path / "model.gguf"
    gguf.touch()
    server = LlamaServer(gguf_path=gguf, port=8200, ctx_size=8192)

    with patch("subprocess.Popen") as mock_popen:
        mock_popen.return_value = MagicMock()
        server.start()

    mock_popen.assert_called_once()
    call_args = mock_popen.call_args
    cmd = call_args[0][0]
    assert "llama-server" in cmd
    assert "--model" in cmd
    assert str(gguf) in cmd
    assert "--port" in cmd
    assert "8200" in cmd
    assert "--ctx-size" in cmd
    assert "8192" in cmd


def test_llama_server_stop_terminates(tmp_path: Path) -> None:
    gguf = tmp_path / "model.gguf"
    gguf.touch()
    server = LlamaServer(gguf_path=gguf, port=8200, ctx_size=8192)

    mock_proc = MagicMock()
    mock_proc.wait.return_value = 0
    server._process = mock_proc

    server.stop()

    mock_proc.terminate.assert_called_once()
    mock_proc.wait.assert_called_once_with(timeout=10)
    assert server._process is None


def test_llama_server_stop_kills_on_timeout(tmp_path: Path) -> None:
    gguf = tmp_path / "model.gguf"
    gguf.touch()
    server = LlamaServer(gguf_path=gguf, port=8200, ctx_size=8192)

    mock_proc = MagicMock()
    mock_proc.wait.side_effect = [
        subprocess.TimeoutExpired(cmd="llama-server", timeout=10),
        0,
    ]
    server._process = mock_proc

    server.stop()

    mock_proc.terminate.assert_called_once()
    mock_proc.kill.assert_called_once()
    assert server._process is None


# ---------------------------------------------------------------------------
# Running Server Detection
# ---------------------------------------------------------------------------


def test_find_running_server_returns_port() -> None:
    mock_proc = MagicMock()
    mock_proc.info = {
        "name": "llama-server",
        "cmdline": ["llama-server", "--port", "8150", "--model", "nemotron.gguf"],
    }

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"data": [{"id": "nemotron-nano-30b"}]}

    import sys

    mock_psutil_module = MagicMock()
    mock_psutil_module.process_iter.return_value = [mock_proc]
    mock_psutil_module.NoSuchProcess = type("NoSuchProcess", (Exception,), {})
    mock_psutil_module.AccessDenied = type("AccessDenied", (Exception,), {})

    mock_httpx_module = MagicMock()
    mock_httpx_module.get.return_value = mock_resp

    original_psutil = sys.modules.get("psutil")
    original_httpx = sys.modules.get("httpx")
    try:
        sys.modules["psutil"] = mock_psutil_module
        sys.modules["httpx"] = mock_httpx_module
        result = _find_running_server()
    finally:
        if original_psutil is None:
            sys.modules.pop("psutil", None)
        else:
            sys.modules["psutil"] = original_psutil
        if original_httpx is None:
            sys.modules.pop("httpx", None)
        else:
            sys.modules["httpx"] = original_httpx

    assert result == 8150


def test_find_running_server_no_psutil() -> None:
    import sys

    original = sys.modules.get("psutil")
    sys.modules["psutil"] = None  # type: ignore[assignment]
    try:
        result = _find_running_server()
    finally:
        if original is None:
            sys.modules.pop("psutil", None)
        else:
            sys.modules["psutil"] = original

    assert result is None


def test_find_running_server_no_matching_model() -> None:
    mock_proc = MagicMock()
    mock_proc.info = {
        "name": "llama-server",
        "cmdline": ["llama-server", "--port", "8150"],
    }

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"data": [{"id": "some-other-model"}]}

    import sys

    mock_psutil_module = MagicMock()
    mock_psutil_module.process_iter.return_value = [mock_proc]
    mock_psutil_module.NoSuchProcess = type("NoSuchProcess", (Exception,), {})
    mock_psutil_module.AccessDenied = type("AccessDenied", (Exception,), {})

    mock_httpx_module = MagicMock()
    mock_httpx_module.get.return_value = mock_resp

    original_psutil = sys.modules.get("psutil")
    original_httpx = sys.modules.get("httpx")
    try:
        sys.modules["psutil"] = mock_psutil_module
        sys.modules["httpx"] = mock_httpx_module
        result = _find_running_server()
    finally:
        if original_psutil is None:
            sys.modules.pop("psutil", None)
        else:
            sys.modules["psutil"] = original_psutil
        if original_httpx is None:
            sys.modules.pop("httpx", None)
        else:
            sys.modules["httpx"] = original_httpx

    assert result is None


# ---------------------------------------------------------------------------
# LLM Call (mocked)
# ---------------------------------------------------------------------------


def _make_llm_response(
    text: str, total_tokens: int = 100, status_code: int = 200
) -> MagicMock:
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    mock_resp.text = ""
    mock_resp.json.return_value = {
        "choices": [{"message": {"content": text}}],
        "usage": {"total_tokens": total_tokens},
        "model": "test-model",
    }
    mock_resp.raise_for_status = MagicMock()
    return mock_resp


def test_call_llm_success() -> None:
    import sys

    from scarecrow.summarizer import _call_llm

    mock_httpx = MagicMock()
    mock_httpx.post.return_value = _make_llm_response("Great summary", 250)
    mock_httpx.TimeoutException = type("TimeoutException", (Exception,), {})
    mock_httpx.ConnectError = type("ConnectError", (Exception,), {})

    original = sys.modules.get("httpx")
    try:
        sys.modules["httpx"] = mock_httpx
        text, _tokens, _model = _call_llm(8200, "system", "user content")
    finally:
        if original is None:
            sys.modules.pop("httpx", None)
        else:
            sys.modules["httpx"] = original

    assert text == "Great summary"
    assert _tokens == 250


def test_call_llm_retries_on_500() -> None:
    import sys

    from scarecrow.summarizer import _call_llm

    error_resp = MagicMock()
    error_resp.status_code = 500
    error_resp.text = "Internal server error"
    error_resp.raise_for_status = MagicMock()

    success_resp = _make_llm_response("Retry succeeded", 100)

    mock_httpx = MagicMock()
    mock_httpx.post.side_effect = [error_resp, success_resp]
    mock_httpx.TimeoutException = type("TimeoutException", (Exception,), {})
    mock_httpx.ConnectError = type("ConnectError", (Exception,), {})

    original = sys.modules.get("httpx")
    try:
        sys.modules["httpx"] = mock_httpx
        with patch("scarecrow.summarizer.time.sleep"):
            text, _tokens, _model = _call_llm(8200, "system", "user content")
    finally:
        if original is None:
            sys.modules.pop("httpx", None)
        else:
            sys.modules["httpx"] = original

    assert text == "Retry succeeded"
    assert mock_httpx.post.call_count == 2


def test_call_llm_exhausts_retries() -> None:
    import sys

    from scarecrow.summarizer import _call_llm

    error_resp = MagicMock()
    error_resp.status_code = 500
    error_resp.text = "Internal server error"
    error_resp.raise_for_status = MagicMock()

    mock_httpx = MagicMock()
    mock_httpx.post.return_value = error_resp
    mock_httpx.TimeoutException = type("TimeoutException", (Exception,), {})
    mock_httpx.ConnectError = type("ConnectError", (Exception,), {})

    original = sys.modules.get("httpx")
    try:
        sys.modules["httpx"] = mock_httpx
        with (
            patch("scarecrow.summarizer.time.sleep"),
            pytest.raises(RuntimeError),
        ):
            _call_llm(8200, "system", "user content")
    finally:
        if original is None:
            sys.modules.pop("httpx", None)
        else:
            sys.modules["httpx"] = original


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

    mock_server = MagicMock()
    mock_server.wait_ready.return_value = True
    mock_server.port = 8200

    with (
        patch("scarecrow.summarizer._find_running_server", return_value=None),
        patch("scarecrow.summarizer._discover_gguf", return_value=fake_gguf),
        patch("scarecrow.summarizer._pick_port", return_value=8200),
        patch("scarecrow.summarizer.LlamaServer", return_value=mock_server),
        patch(
            "scarecrow.summarizer._call_llm",
            return_value=("Summary text here", 500, "nemotron-nano"),
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

    mock_server.start.assert_called_once()
    mock_server.wait_ready.assert_called_once()
    mock_server.stop.assert_called_once()


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

    with (
        patch("scarecrow.summarizer._find_running_server", return_value=None),
        patch("scarecrow.summarizer._discover_gguf", return_value=None),
    ):
        result = summarize_session(tmp_path)

    assert result is not None
    content = result.read_text(encoding="utf-8")
    assert "failed" in content.lower()
    assert "download" in content.lower()


def test_summarize_session_server_timeout(tmp_path: Path) -> None:
    events = [
        {"type": "transcript", "text": "Some speech here."},
    ]
    _write_transcript(tmp_path, events)

    fake_gguf = tmp_path / "model.gguf"
    fake_gguf.touch()

    mock_server = MagicMock()
    mock_server.wait_ready.return_value = False
    mock_server.port = 8200

    with (
        patch("scarecrow.summarizer._find_running_server", return_value=None),
        patch("scarecrow.summarizer._discover_gguf", return_value=fake_gguf),
        patch("scarecrow.summarizer._pick_port", return_value=8200),
        patch("scarecrow.summarizer.LlamaServer", return_value=mock_server),
    ):
        result = summarize_session(tmp_path)

    assert result is not None
    content = result.read_text(encoding="utf-8")
    assert "failed" in content.lower()
    mock_server.stop.assert_called_once()
