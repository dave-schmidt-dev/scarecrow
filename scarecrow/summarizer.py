"""Session summarizer — generates summary.md from transcript.jsonl using a local LLM."""

from __future__ import annotations

import contextlib
import json
import logging
import random
import socket
import subprocess
import time
from pathlib import Path

from scarecrow import config

log = logging.getLogger(__name__)


def _find_running_server() -> int | None:
    try:
        import psutil
    except ImportError:
        return None

    try:
        import httpx

        for proc in psutil.process_iter(["name", "cmdline"]):
            try:
                name = proc.info.get("name") or ""
                cmdline = proc.info.get("cmdline") or []
                cmdline_str = " ".join(cmdline)
                if "llama-server" not in name and "llama-server" not in cmdline_str:
                    continue

                # Extract --port from cmdline
                port: int | None = None
                for i, arg in enumerate(cmdline):
                    if arg == "--port" and i + 1 < len(cmdline):
                        with contextlib.suppress(ValueError):
                            port = int(cmdline[i + 1])
                        break

                if port is None:
                    continue

                # Verify it's responsive and serves the right model
                try:
                    resp = httpx.get(f"http://127.0.0.1:{port}/v1/models", timeout=5)
                    if resp.status_code != 200:
                        continue
                    data = resp.json()
                    models = data.get("data", [])
                    for model in models:
                        model_id = model.get("id", "")
                        nemotron_match = "nemotron" in model_id.lower()
                        alias_match = model_id == config.SUMMARIZER_SERVER_ALIAS
                        if nemotron_match or alias_match:
                            log.info(
                                "Found running llama-server on port %d (model %r)",
                                port,
                                model_id,
                            )
                            return port
                except Exception:
                    continue

            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

    except Exception:
        log.exception("Error while searching for running llama-server")

    return None


def _discover_gguf() -> Path | None:
    hub_root = Path.home() / ".cache" / "huggingface" / "hub"
    pattern = f"models--{config.SUMMARIZER_MODEL_PATTERN}"
    for model_dir in hub_root.glob(pattern):
        for gguf in model_dir.glob("snapshots/*/*.gguf"):
            if gguf.name.startswith("mmproj"):
                continue
            if gguf.name.endswith(".downloadInProgress"):
                continue
            log.debug("Found GGUF: %s", gguf)
            return gguf
    return None


def _model_name_from_gguf(gguf_path: Path) -> str:
    # Walk up to the models--owner--repo-GGUF directory
    for parent in (gguf_path, *gguf_path.parents):
        if parent.name.startswith("models--"):
            # models--unsloth--Nemotron-3-Nano-30B-A3B-GGUF
            parts = parent.name.split("--")
            if len(parts) >= 3:
                repo = "--".join(parts[2:])
                # Strip -GGUF suffix
                if repo.upper().endswith("-GGUF"):
                    repo = repo[: -len("-GGUF")]
                return repo
    return gguf_path.stem


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // config.SUMMARIZER_CHARS_PER_TOKEN)


def _compute_ctx_size(input_tokens: int) -> int:
    needed = input_tokens + 500 + config.SUMMARIZER_OUTPUT_BUDGET
    result = max(config.SUMMARIZER_MIN_CTX, needed)
    result = ((result + 1023) // 1024) * 1024
    result = min(result, 524288)  # 512K hard cap — Nemotron supports 1M
    return result


def _read_events(transcript_path: Path) -> list[dict]:
    events: list[dict] = []
    with transcript_path.open(encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                log.warning(
                    "Skipping malformed JSON at %s line %d", transcript_path, lineno
                )
    return events


_IGNORED_EVENT_TYPES = frozenset(
    {
        "session_start",
        "session_end",
        "session_metrics",
        "recording_start",
        "warning",
    }
)

_SYSTEM_PROMPT_BASE = (
    "You are a meeting/recording summarizer. "
    "You will receive a transcript with interleaved notes.\n\n"
    "Output format (use this exact structure):\n\n"
    "1. **Executive Summary** — 1-3 short paragraphs giving a high-level "
    "overview of what was discussed, decided, or accomplished.\n\n"
    "2. **Key Points** — a bulleted list of the most important points, "
    "decisions, and highlights ONLY. Consolidate related ideas into single "
    "bullets. Do NOT go line-by-line through the transcript — extract and "
    "synthesize the key themes. For a short recording (under 1000 words), "
    "3-5 bullets is usually enough. Scale up proportionally for longer "
    "recordings.\n\n"
    "3. **Tasks** — if any [TASK] entries exist, list ALL of them at the "
    "bottom under a `## Tasks` heading as a Markdown checklist: "
    "`- [ ] task text`. If there are no tasks, omit this section.\n\n"
    "CRITICAL RULES:\n"
    "- ONLY include information that is explicitly stated in the transcript. "
    "Do not infer, guess, embellish, add logical conclusions, or fill in "
    "gaps. If the transcript is unclear or incomplete, summarize what was "
    "actually said, not what was probably meant.\n"
    "- You MAY correct obvious transcription typos, especially for proper "
    "nouns that were garbled by speech-to-text (use context clues and any "
    "[CONTEXT] entries to get names and terms right). But do NOT add any "
    "information, characterizations, or conclusions beyond what was said.\n"
    "- [NOTE] entries are the user's own observations made during recording. "
    "Weave them naturally into the executive summary or key points wherever "
    "they are relevant — do NOT create a separate notes section. Treat them "
    "as first-person insights that enrich the surrounding discussion.\n"
    "- [CONTEXT] entries are background information (spelling hints, "
    "participant names, domain terms). Use them ONLY to improve spelling "
    "and naming accuracy. Do NOT surface context as standalone items, do "
    "NOT mention that context was provided, and do NOT add any information "
    "from context that the speakers did not actually discuss.\n"
    "- Use ## for section headings. Keep the summary concise."
)


def _build_prompt(events: list[dict]) -> tuple[str, str]:
    context_items: list[str] = []
    content_parts: list[str] = []

    for event in events:
        event_type = event.get("type", "")

        if event_type in _IGNORED_EVENT_TYPES:
            continue

        if event_type == "session_renamed":
            name = event.get("name", "").strip()
            if name:
                context_items.append(f"Session name: {name}")
            continue

        if event_type == "transcript":
            text = event.get("text", "").strip()
            if text:
                content_parts.append(text)

        elif event_type == "note":
            tag = event.get("tag", "NOTE").upper()
            text = event.get("text", "").strip()
            if not text:
                continue
            if tag == "CONTEXT":
                context_items.append(text)
            elif tag == "TASK":
                content_parts.append(f"[TASK: {text}]")
            else:
                # NOTE or anything else
                content_parts.append(f"[NOTE: {text}]")

        elif event_type == "divider":
            content_parts.append("")

        elif event_type == "pause":
            content_parts.append("[Recording paused]")

        elif event_type == "resume":
            content_parts.append("[Recording resumed]")

    system_prompt = _SYSTEM_PROMPT_BASE
    if context_items:
        context_block = "\n\nBackground context provided by the user:\n" + "\n".join(
            f"- {item}" for item in context_items
        )
        system_prompt += context_block

    user_content = "\n".join(content_parts)
    return system_prompt, user_content


class LlamaServer:
    """Manages a llama-server subprocess for local LLM inference."""

    def __init__(
        self,
        gguf_path: Path,
        port: int,
        ctx_size: int,
        log_dir: Path | None = None,
    ) -> None:
        self._gguf_path = gguf_path
        self._port = port
        self._ctx_size = ctx_size
        self._log_dir = log_dir
        self._process: subprocess.Popen | None = None
        self._log_fh = None

    @property
    def port(self) -> int:
        return self._port

    def start(self) -> None:
        log_file = (
            self._log_dir / "llama-server.log" if self._log_dir else subprocess.DEVNULL
        )
        cmd = [
            "llama-server",
            "--model",
            str(self._gguf_path),
            "--alias",
            config.SUMMARIZER_SERVER_ALIAS,
            "--port",
            str(self._port),
            "--ctx-size",
            str(self._ctx_size),
            "--flash-attn",
            "on",
            "--reasoning-budget",
            "8192",
            "--jinja",
        ]
        if isinstance(log_file, Path):
            fh = open(log_file, "w")  # noqa: SIM115
            self._log_fh = fh
        else:
            fh = subprocess.DEVNULL
            self._log_fh = None
        self._process = subprocess.Popen(cmd, stdout=fh, stderr=fh)

    def wait_ready(self, timeout: int | None = None) -> bool:
        import httpx

        if timeout is None:
            timeout = config.SUMMARIZER_SERVER_TIMEOUT
        url = f"http://127.0.0.1:{self._port}/health"
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                resp = httpx.get(url, timeout=5)
                if resp.status_code == 200:
                    return True
            except (httpx.ConnectError, httpx.TimeoutException):
                pass
            if self._process and self._process.poll() is not None:
                return False
            time.sleep(2)
        return False

    def stop(self) -> None:
        if self._process is None:
            return
        self._process.terminate()
        try:
            self._process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait(timeout=5)
        self._process = None
        if self._log_fh is not None:
            self._log_fh.close()
            self._log_fh = None


def _pick_port() -> int:
    low, high = config.SUMMARIZER_PORT_RANGE
    for _ in range(3):
        port = random.randint(low, high)
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.bind(("127.0.0.1", port))
            return port
        except OSError:
            continue
    raise RuntimeError(
        f"Could not find a free port in range "
        f"{config.SUMMARIZER_PORT_RANGE} after 3 attempts"
    )


def _call_llm(port: int, system_prompt: str, user_content: str) -> tuple[str, int, str]:
    import httpx

    url = f"http://127.0.0.1:{port}/v1/chat/completions"
    body = {
        "model": config.SUMMARIZER_SERVER_ALIAS,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        "max_tokens": config.SUMMARIZER_OUTPUT_BUDGET,
        "temperature": 0.3,
    }

    last_exc: Exception | None = None
    for attempt in range(config.SUMMARIZER_MAX_RETRIES + 1):
        try:
            resp = httpx.post(url, json=body, timeout=300)
            if resp.status_code >= 500:
                raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")
            resp.raise_for_status()
            data = resp.json()
            text = data["choices"][0]["message"]["content"]
            total_tokens = data.get("usage", {}).get("total_tokens", 0)
            model_name = data.get("model", "unknown")
            return text, total_tokens, model_name
        except (httpx.TimeoutException, httpx.ConnectError, RuntimeError) as exc:
            last_exc = exc
            log.warning(
                "LLM call attempt %d/%d failed: %s",
                attempt + 1,
                config.SUMMARIZER_MAX_RETRIES + 1,
                exc,
            )
            if attempt < config.SUMMARIZER_MAX_RETRIES:
                time.sleep(2)

    raise RuntimeError(
        f"LLM call failed after {config.SUMMARIZER_MAX_RETRIES + 1} "
        f"attempts: {last_exc}"
    )


def _write_error_summary(session_dir: Path, error_msg: str) -> Path:
    summary_path = session_dir / "summary.md"
    content = (
        f"# Summary (failed)\n\n"
        f"Auto-summarization failed: {error_msg}\n\n"
        f"To retry:\n\n"
        f"```\n"
        f"python3 scripts/resummarize.py {session_dir}\n"
        f"```\n"
    )
    summary_path.write_text(content, encoding="utf-8")
    return summary_path


def summarize_session(session_dir: Path) -> Path | None:
    """Generate summary.md for a completed session.

    Reads transcript.jsonl from session_dir, builds a prompt from the events,
    finds or starts a local llama-server running Nemotron, calls the LLM, and
    writes summary.md. Returns the path to summary.md on success, or a path to
    an error summary on failure. Returns None only if an unexpected error occurs
    before any output can be written.
    """
    try:
        transcript_path = session_dir / "transcript.jsonl"
        if not transcript_path.exists():
            return _write_error_summary(session_dir, "No transcript.jsonl found")

        events = _read_events(transcript_path)
        if not events:
            return _write_error_summary(session_dir, "Transcript is empty")

        system_prompt, user_content = _build_prompt(events)
        if not user_content.strip():
            return _write_error_summary(session_dir, "No transcribed speech found")

        input_tokens = _estimate_tokens(system_prompt + user_content)
        ctx_size = _compute_ctx_size(input_tokens)

        # 1. Check for running server FIRST
        port = _find_running_server()
        server = None
        gguf_model_name: str | None = None

        if port is None:
            # 2. Discover model
            gguf = _discover_gguf()
            if gguf is None:
                return _write_error_summary(
                    session_dir,
                    "Nemotron GGUF model not found in ~/.cache/huggingface/hub/. "
                    "Download it first: "
                    "huggingface-cli download unsloth/Nemotron-3-Nano-30B-A3B-GGUF",
                )

            # Derive real model name from GGUF path
            gguf_model_name = _model_name_from_gguf(gguf)

            # 3. Start server
            port = _pick_port()
            server = LlamaServer(gguf, port, ctx_size, log_dir=session_dir)
            log.info("Starting llama-server on port %d (ctx %d)", port, ctx_size)
            server.start()

            if not server.wait_ready():
                server.stop()
                return _write_error_summary(
                    session_dir,
                    f"llama-server failed to become ready within "
                    f"{config.SUMMARIZER_SERVER_TIMEOUT}s. "
                    f"Check {session_dir / 'llama-server.log'} for details.",
                )

        try:
            log.info("Calling LLM for summarization...")
            summary_text, total_tokens, api_model = _call_llm(
                port, system_prompt, user_content
            )

            # Prefer GGUF-derived name; fall back to API response
            model_label = gguf_model_name or api_model
            transcript_words = len(user_content.split())
            summary_words = len(summary_text.split())
            footer = (
                f"\n\n---\n"
                f"*Generated by Scarecrow · "
                f"model: {model_label} · "
                f"{transcript_words} words transcribed, "
                f"summarized in {summary_words} words · "
                f"{total_tokens} tokens used · "
                f"ctx {ctx_size}*\n"
            )
            summary_text += footer

            summary_path = session_dir / "summary.md"
            summary_path.write_text(summary_text, encoding="utf-8")
            log.info("Summary written to %s", summary_path)
            return summary_path
        finally:
            if server is not None:
                server.stop()

    except Exception:
        log.exception("Summarization failed")
        try:
            return _write_error_summary(session_dir, "Unexpected error — see logs")
        except Exception:
            return None
