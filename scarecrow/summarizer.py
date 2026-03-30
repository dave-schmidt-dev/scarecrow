"""Session summarizer — generates summary.md from transcript.jsonl using a local LLM."""

from __future__ import annotations

import json
import logging
import shutil
import time
from pathlib import Path

from scarecrow import config

log = logging.getLogger(__name__)


def _sync_to_obsidian(
    summary_path: Path, session_name: str, vault: Path | None
) -> None:
    """Copy summary to Obsidian vault if configured."""
    if vault is None or not vault.is_dir():
        return
    dest = vault / f"{session_name}.md"
    try:
        shutil.copy2(summary_path, dest)
        log.info("Summary synced to Obsidian: %s", dest)
    except OSError:
        log.warning("Failed to sync summary to Obsidian", exc_info=True)


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
    "Summarize the transcript below. Use ONLY information explicitly stated "
    "in the transcript. Fix obvious speech-to-text typos for names and terms.\n\n"
    "Output ONLY the structured summary below — no preamble, no reasoning, "
    "no thinking. Start directly with ## Summary.\n\n"
    "Use this exact output structure:\n\n"
    "## Summary\n"
    "1-3 short paragraphs: what was discussed, decided, or accomplished.\n\n"
    "## Key Points\n"
    "- Bulleted list of important points, decisions, and highlights.\n"
    "- Synthesize related ideas into single bullets.\n"
    "- Short recordings: 3-5 bullets. Scale up for longer ones.\n\n"
    "## Action Items\n"
    "- [ ] Each action item as a Markdown checkbox.\n"
    "Include every [TASK] entry verbatim, plus any commitments or follow-ups "
    'from the conversation (e.g. "I\'ll send that over", "we need to update '
    'the docs"). Omit this section if there are none.\n\n'
    "Tag handling:\n"
    "- [NOTE]: User observations. Weave into Summary or Key Points naturally.\n"
    "- [TASK]: Copy verbatim into Action Items.\n"
    "- [CONTEXT]: Spelling hints and background. Use to fix names and terms "
    "only. Never surface as standalone content.\n\n"
    "Output ONLY the three sections above. Do not add any other sections."
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


def _strip_reasoning(text: str) -> str:
    """Remove chain-of-thought reasoning from Nemotron-Nano output.

    The model may wrap reasoning in <think>...</think> tags, or emit
    free-form reasoning before the actual structured summary.  We try
    several strategies in order:
      1. Strip <think>…</think> blocks (may appear at the start).
      2. Keep only text from the first ## heading onward.
      3. If neither works, the model produced only reasoning — return
         an error placeholder so the user knows to re-run.
    """
    import re

    # 1. Strip <think>…</think> blocks (greedy within each block).
    stripped = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    if stripped:
        text = stripped

    # 2. Keep from first ## heading onward.
    heading_match = re.search(r"(?m)^## ", text)
    if heading_match:
        return text[heading_match.start() :]

    # 3. No heading found — model produced only reasoning.
    log.warning("Summarizer output contained no ## headings; treating as failed")
    return ""


def _generate(
    gguf_path: Path,
    system_prompt: str,
    user_content: str,
    ctx_size: int,
) -> tuple[str, int]:
    """Load model in-process and generate a chat completion.

    Returns (generated_text, total_tokens).
    """
    from llama_cpp import Llama

    llm = Llama(
        model_path=str(gguf_path),
        n_ctx=ctx_size,
        n_gpu_layers=-1,
        flash_attn=True,
        verbose=False,
    )
    try:
        # First try chat completion.
        response = llm.create_chat_completion(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            max_tokens=config.SUMMARIZER_OUTPUT_BUDGET,
            temperature=0.3,
        )
        raw_text = response["choices"][0]["message"]["content"]
        log.debug("Raw model output (first 500 chars): %s", raw_text[:500])
        text = _strip_reasoning(raw_text)
        usage = response.get("usage", {})
        total_tokens = usage.get("total_tokens", 0)

        if text:
            return text, total_tokens

        # Model produced only reasoning / meta-commentary.  Retry with
        # raw completion and a forced prefix so it cannot preamble.
        log.warning("Chat completion produced no summary; retrying with forced prefix")
        prefix = "## Summary\n\n"
        prompt = (
            f"<|system|>\n{system_prompt}\n"
            f"<|user|>\n{user_content}\n"
            f"<|assistant|>\n{prefix}"
        )
        raw = llm(
            prompt,
            max_tokens=config.SUMMARIZER_OUTPUT_BUDGET,
            temperature=0.1,
            stop=["<|end|>", "<|user|>", "<|system|>"],
        )
        raw_text2 = raw["choices"][0]["text"]
        log.debug("Retry raw output (first 500 chars): %s", raw_text2[:500])
        text = _strip_reasoning(prefix + raw_text2)
        usage2 = raw.get("usage", {})
        total_tokens = usage2.get("total_tokens", total_tokens)
        if text:
            return text, total_tokens

        # Both attempts failed — return error placeholder.
        fail_msg = (
            "## Summary\n\n"
            "*Auto-summarization produced only reasoning with no structured output. "
            "Re-run with `scripts/resummarize.py` to retry.*\n"
        )
        return fail_msg, total_tokens
    finally:
        del llm


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


def summarize_session(
    session_dir: Path, *, obsidian_dir: Path | None = None
) -> Path | None:
    """Generate summary.md for a completed session.

    Reads transcript.jsonl from session_dir, builds a prompt from the events,
    loads a local GGUF model in-process via llama-cpp-python, generates a
    summary, and writes summary.md. Returns the path to summary.md on success,
    or a path to an error summary on failure. Returns None only if an unexpected
    error occurs before any output can be written.
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

        gguf = _discover_gguf()
        if gguf is None:
            return _write_error_summary(
                session_dir,
                "Nemotron GGUF model not found in ~/.cache/huggingface/hub/. "
                "Download it first: "
                "huggingface-cli download unsloth/Nemotron-3-Nano-30B-A3B-GGUF",
            )

        model_name = _model_name_from_gguf(gguf)
        log.info("Loading %s (ctx %d) for summarization...", model_name, ctx_size)

        t0 = time.monotonic()
        summary_text, total_tokens = _generate(
            gguf, system_prompt, user_content, ctx_size
        )
        summarize_seconds = time.monotonic() - t0

        transcript_words = len(user_content.split())
        summary_words = len(summary_text.split())
        footer = (
            f"\n\n---\n"
            f"*Generated by Scarecrow · "
            f"model: {model_name} · "
            f"{transcript_words} words transcribed, "
            f"summarized in {summary_words} words · "
            f"{total_tokens} tokens used · "
            f"ctx {ctx_size} · "
            f"{summarize_seconds:.1f}s*\n"
        )
        summary_text += footer

        summary_path = session_dir / "summary.md"
        summary_path.write_text(summary_text, encoding="utf-8")
        log.info("Summary written to %s", summary_path)

        vault = obsidian_dir
        _sync_to_obsidian(summary_path, session_dir.name, vault)

        return summary_path

    except Exception:
        log.exception("Summarization failed")
        try:
            return _write_error_summary(session_dir, "Unexpected error — see logs")
        except Exception:
            return None
