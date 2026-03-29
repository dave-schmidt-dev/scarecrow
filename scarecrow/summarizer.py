"""Session summarizer — generates summary.md from transcript.jsonl using a local LLM."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from scarecrow import config

log = logging.getLogger(__name__)


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
        response = llm.create_chat_completion(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            max_tokens=config.SUMMARIZER_OUTPUT_BUDGET,
            temperature=0.3,
        )
        text = response["choices"][0]["message"]["content"]
        # Nemotron-Nano may emit chain-of-thought reasoning before the
        # actual summary. Strip everything before the first ## heading.
        import re

        heading_match = re.search(r"(?m)^## ", text)
        if heading_match:
            text = text[heading_match.start() :]
        usage = response.get("usage", {})
        total_tokens = usage.get("total_tokens", 0)
        return text, total_tokens
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


def summarize_session(session_dir: Path) -> Path | None:
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

        summary_text, total_tokens = _generate(
            gguf, system_prompt, user_content, ctx_size
        )

        transcript_words = len(user_content.split())
        summary_words = len(summary_text.split())
        footer = (
            f"\n\n---\n"
            f"*Generated by Scarecrow · "
            f"model: {model_name} · "
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

    except Exception:
        log.exception("Summarization failed")
        try:
            return _write_error_summary(session_dir, "Unexpected error — see logs")
        except Exception:
            return None
