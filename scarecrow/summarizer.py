"""Session summarizer — generates summary.md from transcript.jsonl using a local LLM.

Supports two backends:
- **gguf** (default): llama-cpp-python with GGUF model files.
- **mlx**: mlx-vlm for Apple Silicon native inference with optional TurboQuant
  KV-cache compression.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import time
from collections.abc import Callable
from pathlib import Path

from scarecrow import config
from scarecrow.task_review import load_task_review

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Obsidian sync
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# GGUF model discovery
# ---------------------------------------------------------------------------

_MODEL_PATTERNS: dict[str, str] = {
    "gemma4": "*gemma-4-*-GGUF",
}


def _discover_gguf(model_pattern: str | None = None) -> Path | None:
    hub_root = Path.home() / ".cache" / "huggingface" / "hub"
    pattern = f"models--{model_pattern or config.SUMMARIZER_MODEL_PATTERN}"
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
            # models--owner--repo-name-GGUF
            parts = parent.name.split("--")
            if len(parts) >= 3:
                repo = "--".join(parts[2:])
                # Strip -GGUF suffix
                if repo.upper().endswith("-GGUF"):
                    repo = repo[: -len("-GGUF")]
                return repo
    return gguf_path.stem


# ---------------------------------------------------------------------------
# Token / context helpers
# ---------------------------------------------------------------------------


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // config.SUMMARIZER_CHARS_PER_TOKEN)


def _compute_ctx_size(input_tokens: int) -> int:
    needed = input_tokens + 500 + config.SUMMARIZER_OUTPUT_BUDGET
    result = needed
    result = ((result + 1023) // 1024) * 1024
    result = min(result, 524288)  # 512K hard cap
    return result


# ---------------------------------------------------------------------------
# Transcript reading & prompt construction
# ---------------------------------------------------------------------------


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
        "recording_start",
        "warning",
        "segment_boundary",
    }
)

_SYSTEM_PROMPT_TEMPLATE = (
    "Summarize the transcript below. Use ONLY information explicitly stated "
    "in the transcript. Fix obvious speech-to-text typos for names and terms.\n\n"
    "Output ONLY the structured summary below — no preamble, no reasoning, "
    "no thinking. Start directly with ## Summary.\n\n"
    "Use this exact output structure:\n\n"
    "## Summary\n"
    "{summary_guidance}\n\n"
    "## Key Points\n"
    "- Bulleted list of important points, decisions, and highlights.\n"
    "- Synthesize related ideas into single bullets.\n"
    "- {key_points_guidance}\n\n"
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

_SYNTHESIS_PROMPT = (
    "You are given per-segment summaries of a single session. "
    "Synthesize them into ONE cohesive summary that reads as a unified document.\n\n"
    "Output ONLY the structured summary below — no preamble, no reasoning, "
    "no thinking. Start directly with ## Summary.\n\n"
    "Use this exact output structure:\n\n"
    "## Summary\n"
    "Write a flowing narrative covering ALL topics from every segment. "
    "Organize by topic, not by segment. Merge related threads that span "
    "segments into unified paragraphs. Do not reference segment numbers.\n"
    "IMPORTANT: Do not omit any topic, case, project, or discussion point "
    "that appears in the segment summaries. Every detail matters — if a "
    "segment mentions it, the synthesis must include it.\n\n"
    "## Key Points\n"
    "- Bulleted list of important points, decisions, and highlights.\n"
    "- Deduplicate points that appear in multiple segments.\n"
    "- Use **bold labels** for each point.\n"
    "- Include ALL key points from every segment. Do not drop items for brevity.\n\n"
    "## Action Items\n"
    "- [ ] Each action item as a Markdown checkbox.\n"
    "Include all action items from every segment. Deduplicate exact matches "
    "but keep distinct items even if similar.\n"
    "Omit this section if there are none.\n\n"
    "Preserve speaker attribution from per-segment summaries. If the same "
    "person appears with different labels across segments, unify them where "
    "possible.\n\n"
    "Output ONLY the three sections above. Do not add any other sections."
)


def _scale_prompt(transcript_words: int) -> str:
    """Return the system prompt with detail guidance scaled to transcript length."""
    if transcript_words < 500:
        summary_guidance = (
            "1-2 short paragraphs: what was discussed, decided, or accomplished."
        )
        key_points_guidance = "3-5 bullets."
    elif transcript_words < 3000:
        summary_guidance = (
            "2-3 paragraphs: what was discussed, decided, or accomplished."
        )
        key_points_guidance = "5-8 bullets."
    elif transcript_words < 8000:
        summary_guidance = (
            "3-5 paragraphs covering all major topics discussed. "
            "Give each distinct topic its own paragraph."
        )
        key_points_guidance = "8-12 bullets. Use **bold labels** for each point."
    else:
        summary_guidance = (
            "5-7 paragraphs covering all major topics discussed. "
            "Give each distinct topic or segment its own paragraph. "
            "Do not omit topics for brevity."
        )
        key_points_guidance = (
            "12-18 bullets. Use **bold labels** for each point. "
            "Cover every significant topic."
        )
    return _SYSTEM_PROMPT_TEMPLATE.format(
        summary_guidance=summary_guidance,
        key_points_guidance=key_points_guidance,
    )


def _fmt_duration(seconds: int) -> str:
    """Format a session duration in seconds to a human-readable string."""
    if seconds < 60:
        return f"{seconds}s" if seconds > 0 else "0s"
    h, m = divmod(seconds // 60, 60)
    return f"{h}h {m}m" if h else f"{m} min"


def _build_prompt(events: list[dict]) -> tuple[str, str, int]:
    context_items: list[str] = []
    content_parts: list[str] = []
    elapsed_seconds = 0
    has_speaker_labels = False

    for event in events:
        event_type = event.get("type", "")

        if event_type == "session_metrics":
            elapsed_seconds = int(event.get("elapsed", 0))
            continue

        if event_type in _IGNORED_EVENT_TYPES:
            continue

        if event_type == "session_renamed":
            name = event.get("name", "").strip()
            if name:
                context_items.append(f"Session name: {name}")
            continue

        if event_type == "transcript":
            text = event.get("text", "").strip()
            if not text:
                continue
            speaker = event.get("speaker")
            if speaker:
                content_parts.append(f"[{speaker}]: {text}")
                has_speaker_labels = True
            else:
                content_parts.append(text)

        elif event_type == "note":
            tag = event.get("tag", "NOTE").upper()
            if tag == "SPEAKERS":
                continue  # consumed by diarization pipeline
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

        elif event_type in ("mute", "unmute"):
            source = event.get("source", "mic")
            label = "Mic" if source == "mic" else "Sys audio"
            action = "muted" if event_type == "mute" else "unmuted"
            content_parts.append(f"[{label} {action}]")

    user_content = "\n".join(content_parts)
    # Exclude [Speaker]: prefixes from word count for scaling
    transcript_words = len(_strip_speaker_prefixes(user_content).split())

    system_prompt = _scale_prompt(transcript_words)

    if has_speaker_labels:
        system_prompt += (
            "\n\nSpeaker attribution: The transcript uses speaker labels from "
            "automatic diarization. Attribute statements and commitments to "
            'specific speakers. Write naturally: "Mike proposed X" not '
            '"[Speaker A] proposed X".'
        )

    if context_items:
        context_block = "\n\nBackground context provided by the user:\n" + "\n".join(
            f"- {item}" for item in context_items
        )
        system_prompt += context_block

    return system_prompt, user_content, elapsed_seconds


def _strip_speaker_prefixes(text: str) -> str:
    """Remove [Speaker]: prefixes from text for word counting."""
    import re

    return re.sub(r"\[[^\]]+\]: ", "", text)


def _validated_task_guidance(session_dir: Path) -> str:
    """Return prompt guidance when reviewed tasks exist for this session."""
    review = load_task_review(session_dir)
    if review is None:
        return ""

    tasks = [
        task.get("text", "").strip()
        for task in review.get("tasks", [])
        if task.get("text", "").strip()
    ]
    if not tasks:
        return (
            "\n\nValidated action items:\n"
            "The user completed task review for this session and confirmed that "
            "there are no valid action items. Omit the `## Action Items` section."
        )

    task_lines = "\n".join(f"- [ ] {task}" for task in tasks)
    return (
        "\n\nValidated action items:\n"
        "The user reviewed the task list for this session. Use EXACTLY the "
        "items below in `## Action Items`, preserve wording, and do not add "
        "or infer any other action items.\n\n"
        f"{task_lines}"
    )


def _review_feedback_guidance(review_feedback: str | None) -> str:
    """Return prompt guidance from user-provided summary review feedback."""
    if review_feedback is None:
        return ""

    feedback = review_feedback.strip()
    if not feedback:
        return ""

    return (
        "\n\nSummary review feedback from the user:\n"
        "Revise the summary to address the requested corrections below. Use ONLY "
        "facts supported by the transcript and context notes. Fix errors, add "
        "missing details when supported, and remove unsupported claims.\n\n"
        f"{feedback}"
    )


def _extract_participants(events: list[dict]) -> list[str]:
    """Extract speaker names from SPEAKERS notes."""
    names: list[str] = []
    seen: set[str] = set()
    for ev in events:
        if ev.get("type") != "note" or ev.get("tag", "").upper() != "SPEAKERS":
            continue
        text = ev.get("text", "")
        # Parse "mic:Name1,Name2 sys:Name3,Name4"
        for part in text.split():
            if ":" in part:
                _, speaker_list = part.split(":", 1)
                for name in speaker_list.split(","):
                    name = name.strip()
                    if name and name.lower() not in seen:
                        seen.add(name.lower())
                        names.append(name)
    return names


def _apply_speaker_labels(
    events: list[dict], session_dir: Path, segment: int, segment_elapsed_offset: int = 0
) -> list[dict]:
    """Apply speaker labels from diarization sidecars if available."""
    try:
        from scarecrow.diarizer import label_events

        return label_events(events, session_dir, segment, segment_elapsed_offset)
    except Exception:
        log.debug("No diarization labels applied", exc_info=True)
        return events


# ---------------------------------------------------------------------------
# Output cleanup
# ---------------------------------------------------------------------------


def _strip_reasoning(text: str) -> str:
    """Remove chain-of-thought reasoning from model output.

    Some models wrap reasoning in <think>...</think> tags, or emit
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


# ---------------------------------------------------------------------------
# GGUF backend (llama-cpp-python)
# ---------------------------------------------------------------------------


def _load_model(gguf_path: Path, ctx_size: int):
    """Load a Llama model. Caller owns the returned object's lifetime."""
    from llama_cpp import Llama

    return Llama(
        model_path=str(gguf_path),
        n_ctx=ctx_size,
        n_gpu_layers=-1,
        flash_attn=True,
        verbose=False,
    )


def _generate(
    gguf_path: Path,
    system_prompt: str,
    user_content: str,
    ctx_size: int,
    *,
    llm=None,
) -> tuple[str, int]:
    """Generate a chat completion. Loads model if *llm* is not provided.

    Returns (generated_text, total_tokens).
    """
    owns_llm = llm is None
    if owns_llm:
        llm = _load_model(gguf_path, ctx_size)
    try:
        # Chat completion — works for all models via embedded chat template.
        response = llm.create_chat_completion(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            max_tokens=config.SUMMARIZER_OUTPUT_BUDGET,
            temperature=0.3,
            repeat_penalty=1.1,
            top_k=40,
            top_p=0.95,
        )
        raw_text = response["choices"][0]["message"]["content"]
        log.debug("Raw model output (first 500 chars): %s", raw_text[:500])
        text = _strip_reasoning(raw_text)
        usage = response.get("usage", {})
        total_tokens = usage.get("total_tokens", 0)

        if text:
            return text, total_tokens

        # Model produced only reasoning / meta-commentary.  Retry with
        # a second chat completion that includes the forced prefix as an
        # assistant message start.  This is model-agnostic (the GGUF
        # chat template handles token wrapping).
        log.warning("Chat completion produced no summary; retrying with forced prefix")
        prefix = "## Summary\n\n"
        response2 = llm.create_chat_completion(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
                {"role": "assistant", "content": prefix},
            ],
            max_tokens=config.SUMMARIZER_OUTPUT_BUDGET,
            temperature=0.1,
            repeat_penalty=1.1,
            top_k=40,
            top_p=0.95,
        )
        raw_text2 = response2["choices"][0]["message"]["content"]
        log.debug("Retry raw output (first 500 chars): %s", raw_text2[:500])
        text = _strip_reasoning(prefix + raw_text2)
        usage2 = response2.get("usage", {})
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
        if owns_llm:
            del llm


class _GgufBackend:
    """GGUF-based summarizer backend using llama-cpp-python."""

    def __init__(self, gguf_path: Path, ctx_size: int) -> None:
        self._gguf_path = gguf_path
        self._ctx_size = ctx_size
        self._llm = None

    @property
    def name(self) -> str:
        return _model_name_from_gguf(self._gguf_path)

    @property
    def footer_info(self) -> str:
        return f"ctx {self._ctx_size}"

    def load(self) -> None:
        self._llm = _load_model(self._gguf_path, self._ctx_size)

    def generate(self, system_prompt: str, user_content: str) -> tuple[str, int]:
        return _generate(
            self._gguf_path,
            system_prompt,
            user_content,
            self._ctx_size,
            llm=self._llm,
        )

    def close(self) -> None:
        if self._llm is not None:
            del self._llm
            self._llm = None


# ---------------------------------------------------------------------------
# MLX backend (mlx-vlm)
# ---------------------------------------------------------------------------


class _MlxBackend:
    """MLX-based summarizer backend using mlx-vlm for Apple Silicon."""

    def __init__(self, model_id: str, kv_bits: float | None = None) -> None:
        self._model_id = model_id
        self._kv_bits = kv_bits
        self._model = None
        self._processor = None

    @property
    def name(self) -> str:
        return self._model_id.split("/")[-1]

    @property
    def footer_info(self) -> str:
        if self._kv_bits is not None:
            return f"mlx · kv_bits {self._kv_bits}"
        return "mlx"

    def load(self) -> None:
        try:
            from mlx_vlm import load
        except ImportError as e:
            raise ImportError(
                "mlx-vlm is required for the MLX summarizer backend. "
                "Install with: uv sync"
            ) from e

        # Temporarily allow HF Hub network access — runtime.py sets
        # HF_HUB_OFFLINE=1 at startup to prevent implicit downloads
        # during recording.  The summarizer runs post-exit so this is safe.
        old_offline = os.environ.pop("HF_HUB_OFFLINE", None)
        try:
            log.info(
                "Loading MLX model %s (kv_bits=%s)...",
                self._model_id,
                self._kv_bits,
            )
            self._model, self._processor = load(self._model_id)
        finally:
            if old_offline is not None:
                os.environ["HF_HUB_OFFLINE"] = old_offline

    def generate(self, system_prompt: str, user_content: str) -> tuple[str, int]:
        try:
            from mlx_vlm import generate as mlx_generate
            from mlx_vlm.prompt_utils import apply_chat_template
        except ImportError as e:
            raise ImportError(
                "mlx-vlm is required for the MLX summarizer backend. "
                "Install with: uv sync"
            ) from e

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]
        formatted_prompt = apply_chat_template(
            self._processor,
            self._model.config,
            messages,
            num_images=0,
            add_generation_prompt=True,
        )
        kv_kwargs = {}
        if self._kv_bits is not None:
            kv_kwargs["kv_bits"] = self._kv_bits
            kv_kwargs["kv_quant_scheme"] = "turboquant"
        result = mlx_generate(
            self._model,
            self._processor,
            formatted_prompt,
            max_tokens=config.SUMMARIZER_OUTPUT_BUDGET,
            temperature=0.3,
            repetition_penalty=1.1,
            top_p=0.95,
            verbose=False,
            **kv_kwargs,
        )

        raw_text = result.text
        total_tokens = getattr(result, "total_tokens", 0) or (
            getattr(result, "prompt_tokens", 0)
            + getattr(result, "generation_tokens", 0)
        )
        if not total_tokens:
            total_tokens = _estimate_tokens(system_prompt + user_content + raw_text)

        log.debug("MLX raw output (first 500 chars): %s", raw_text[:500])
        text = _strip_reasoning(raw_text)
        if text:
            return text, total_tokens

        # Retry with forced prefix — include assistant start in messages.
        log.warning("MLX completion produced no summary; retrying with forced prefix")
        prefix = "## Summary\n\n"
        messages_retry = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
            {"role": "assistant", "content": prefix},
        ]
        formatted_retry = apply_chat_template(
            self._processor,
            self._model.config,
            messages_retry,
            num_images=0,
        )
        result2 = mlx_generate(
            self._model,
            self._processor,
            formatted_retry,
            max_tokens=config.SUMMARIZER_OUTPUT_BUDGET,
            temperature=0.1,
            repetition_penalty=1.1,
            top_p=0.95,
            verbose=False,
            **kv_kwargs,
        )
        raw_text2 = result2.text
        text = _strip_reasoning(prefix + raw_text2)
        if text:
            return text, total_tokens

        fail_msg = (
            "## Summary\n\n"
            "*Auto-summarization produced only reasoning with no structured output. "
            "Re-run with `scripts/resummarize.py` to retry.*\n"
        )
        return fail_msg, total_tokens

    def close(self) -> None:
        if self._model is not None:
            del self._model
            self._model = None
        if self._processor is not None:
            del self._processor
            self._processor = None


# ---------------------------------------------------------------------------
# Error summary
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Backend factory
# ---------------------------------------------------------------------------


def _create_backend(
    backend_name: str | None = None,
    *,
    model: str | None = None,
    ctx_size: int | None = None,
) -> _GgufBackend | _MlxBackend:
    """Create a summarizer backend.

    Args:
        backend_name: ``"gguf"`` or ``"mlx"``.  Defaults to config value.
        model: Key from ``_MODEL_PATTERNS`` (GGUF only).
        ctx_size: Context window size (GGUF only).

    Raises:
        ValueError: If backend/model combination is invalid.
        FileNotFoundError: If GGUF model not found in HuggingFace cache.
    """
    backend_name = backend_name or config.SUMMARIZER_BACKEND

    if backend_name == "mlx":
        return _MlxBackend(
            config.SUMMARIZER_MLX_MODEL_ID,
            config.SUMMARIZER_MLX_KV_BITS,
        )

    # GGUF backend
    model_pattern = _MODEL_PATTERNS.get(model) if model else None
    gguf = _discover_gguf(model_pattern=model_pattern)
    if gguf is None:
        model_label = model or "default"
        raise FileNotFoundError(
            f"No GGUF model found for '{model_label}' in "
            f"~/.cache/huggingface/hub/. Check download."
        )
    if ctx_size is None:
        raise ValueError("ctx_size is required for GGUF backend")
    return _GgufBackend(gguf, ctx_size)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def summarize_session(
    session_dir: Path,
    *,
    obsidian_dir: Path | None = None,
    model: str | None = None,
    output_name: str = "summary.md",
    backend: str | None = None,
    progress_callback: Callable[[str], None] | None = None,
    review_feedback: str | None = None,
) -> Path | None:
    """Generate summary.md for a completed session.

    Reads transcript.jsonl from session_dir, builds a prompt from the events,
    loads a local LLM, generates a summary, and writes summary.md.  Returns
    the path to summary.md on success, or a path to an error summary on
    failure.  Returns None only if an unexpected error occurs before any
    output can be written.

    Args:
        model: Key from _MODEL_PATTERNS (e.g. "gemma") or None for default.
               GGUF backend only.
        output_name: Output filename — allows side-by-side benchmarking
                     (e.g. "summary_gemma.md") without overwriting production
                     summaries.
        backend: ``"gguf"`` or ``"mlx"``.  Defaults to config value.
    """

    def _progress(msg: str) -> None:
        log.info(msg)
        if progress_callback:
            progress_callback(msg)

    try:
        transcript_path = session_dir / "transcript.jsonl"
        if not transcript_path.exists():
            return _write_error_summary(session_dir, "No transcript.jsonl found")

        events = _read_events(transcript_path)
        if not events:
            return _write_error_summary(session_dir, "Transcript is empty")

        participants = _extract_participants(events)

        # Apply speaker labels from diarization (if available)
        events = _apply_speaker_labels(events, session_dir, segment=1)

        system_prompt, user_content, elapsed_seconds = _build_prompt(events)
        system_prompt += _validated_task_guidance(session_dir)
        system_prompt += _review_feedback_guidance(review_feedback)
        if not user_content.strip():
            return _write_error_summary(session_dir, "No transcribed speech found")

        input_tokens = _estimate_tokens(system_prompt + user_content)
        ctx_size = _compute_ctx_size(input_tokens)

        try:
            be = _create_backend(backend, model=model, ctx_size=ctx_size)
        except (ValueError, FileNotFoundError) as exc:
            return _write_error_summary(session_dir, str(exc))

        model_name = be.name
        _progress(f"Loading summarization model ({model_name})…")

        be.load()
        try:
            _progress("Generating summary…")
            t0 = time.monotonic()
            summary_text, total_tokens = be.generate(system_prompt, user_content)
            summarize_seconds = time.monotonic() - t0
        finally:
            be.close()

        transcript_words = len(user_content.split())
        summary_words = len(summary_text.split())
        participants_line = (
            f"participants: {', '.join(participants)} · " if participants else ""
        )
        footer = (
            f"\n\n---\n"
            f"*Generated by Scarecrow · "
            f"model: {model_name} · "
            f"{transcript_words} words transcribed, "
            f"summarized in {summary_words} words · "
            f"{total_tokens} tokens used · "
            f"{be.footer_info} · "
            f"{summarize_seconds:.1f}s · "
            f"{participants_line}"
            f"session: {_fmt_duration(elapsed_seconds)}*\n"
        )
        summary_text += footer

        summary_path = session_dir / output_name
        summary_path.write_text(summary_text, encoding="utf-8")

        _progress(
            f"Summary complete — {summary_words} words, "
            f"{summarize_seconds:.1f}s ({model_name})"
        )

        # Only sync to Obsidian for production summaries
        if output_name == "summary.md":
            _sync_to_obsidian(summary_path, session_dir.name, obsidian_dir)

        return summary_path

    except Exception:
        log.exception("Summarization failed")
        try:
            return _write_error_summary(session_dir, "Unexpected error — see logs")
        except Exception:
            return None


def _extract_segment_events(events: list[dict], n_segments: int) -> list[list[dict]]:
    """Split *events* into per-segment lists at segment_boundary markers."""
    segments: list[list[dict]] = [[]]
    for ev in events:
        if ev.get("type") == "segment_boundary":
            segments.append([])
        else:
            segments[-1].append(ev)
    # Pad if fewer boundaries than expected
    while len(segments) < n_segments:
        segments.append([])
    return segments[:n_segments]


def _summarize_events(
    session_dir: Path,
    events: list[dict],
    output_name: str,
    be: _GgufBackend | _MlxBackend,
    *,
    review_feedback: str | None = None,
) -> tuple[Path | None, str]:
    """Summarize a list of events and write to *output_name*.

    Returns (path, summary_text).  The backend must already be loaded.
    """
    system_prompt, user_content, _ = _build_prompt(events)
    system_prompt += _review_feedback_guidance(review_feedback)
    if not user_content.strip():
        return None, ""

    t0 = time.monotonic()
    summary_text, total_tokens = be.generate(system_prompt, user_content)
    elapsed = time.monotonic() - t0

    transcript_words = len(user_content.split())
    summary_words = len(summary_text.split())
    footer = (
        f"\n\n---\n"
        f"*Generated by Scarecrow · "
        f"model: {be.name} · "
        f"{transcript_words} words transcribed, "
        f"summarized in {summary_words} words · "
        f"{total_tokens} tokens used · "
        f"{be.footer_info} · "
        f"{elapsed:.1f}s*\n"
    )
    summary_text += footer

    out_path = session_dir / output_name
    out_path.write_text(summary_text, encoding="utf-8")
    log.info("Segment summary written to %s", out_path)
    return out_path, summary_text


def summarize_session_segments(
    session_dir: Path,
    n_segments: int,
    *,
    obsidian_dir: Path | None = None,
    backend: str | None = None,
    progress_callback: Callable[[str], None] | None = None,
    review_feedback: str | None = None,
) -> Path | None:
    """Summarize a multi-segment session.

    For single-segment sessions (n_segments=1) delegates to
    ``summarize_session()`` for backward compatibility.

    For multi-segment sessions: loads the model once, generates one summary
    per segment (``summary_seg1.md``, ``summary_seg2.md``, …), then
    concatenates them into ``summary.md`` with segment headers.
    """
    if n_segments <= 1:
        return summarize_session(
            session_dir,
            obsidian_dir=obsidian_dir,
            backend=backend,
            progress_callback=progress_callback,
            review_feedback=review_feedback,
        )

    def _progress(msg: str) -> None:
        log.info(msg)
        if progress_callback:
            progress_callback(msg)

    try:
        transcript_path = session_dir / "transcript.jsonl"
        if not transcript_path.exists():
            return _write_error_summary(session_dir, "No transcript.jsonl found")

        events = _read_events(transcript_path)
        if not events:
            return _write_error_summary(session_dir, "Transcript is empty")

        participants = _extract_participants(events)

        # Compute segment elapsed offsets for diarization alignment
        seg_offsets = [0]
        for ev in events:
            if ev.get("type") == "segment_boundary":
                seg_offsets.append(int(ev.get("elapsed", 0)))
        while len(seg_offsets) < n_segments:
            seg_offsets.append(seg_offsets[-1] if seg_offsets else 0)

        # Estimate ctx from the largest segment (GGUF needs this at load time)
        segment_events = _extract_segment_events(events, n_segments)

        # Apply speaker labels per segment
        for i, seg_evs in enumerate(segment_events):
            segment_events[i] = _apply_speaker_labels(
                seg_evs,
                session_dir,
                segment=i + 1,
                segment_elapsed_offset=seg_offsets[i],
            )

        max_tokens = 0
        for seg_evs in segment_events:
            sp, uc, _ = _build_prompt(seg_evs)
            max_tokens = max(max_tokens, _estimate_tokens(sp + uc))
        ctx_size = _compute_ctx_size(max_tokens)

        try:
            be = _create_backend(backend, ctx_size=ctx_size)
        except (ValueError, FileNotFoundError) as exc:
            return _write_error_summary(session_dir, str(exc))

        _progress(f"Loading summarization model ({be.name})…")
        be.load()
        try:
            seg_summaries: list[str] = []
            for i, seg_evs in enumerate(segment_events, 1):
                seg_name = f"summary_seg{i}.md"
                if not seg_evs:
                    placeholder = "## Summary\n\nNo speech detected in this segment.\n"
                    (session_dir / seg_name).write_text(placeholder, encoding="utf-8")
                    continue
                _progress(f"Summarizing segment {i}/{n_segments}…")
                _, seg_text = _summarize_events(
                    session_dir,
                    seg_evs,
                    seg_name,
                    be,
                    review_feedback=review_feedback,
                )
                if seg_text:
                    seg_summaries.append(seg_text)
                else:
                    placeholder = "## Summary\n\nNo speech detected in this segment.\n"
                    (session_dir / seg_name).write_text(placeholder, encoding="utf-8")

            if not seg_summaries:
                return _write_error_summary(session_dir, "No segments produced output")

            # Synthesis pass: merge segment summaries into one cohesive summary
            _progress(f"Synthesizing {len(seg_summaries)} segments…")
            separator = "\n\n---\n\n"
            synthesis_input = separator.join(
                f"### Segment {i}\n\n{s}" for i, s in enumerate(seg_summaries, 1)
            )
            t0 = time.monotonic()
            synthesis_prompt = (
                _SYNTHESIS_PROMPT
                + _validated_task_guidance(session_dir)
                + _review_feedback_guidance(review_feedback)
            )
            raw_overall, overall_tokens = be.generate(synthesis_prompt, synthesis_input)
            synthesis_seconds = time.monotonic() - t0
        finally:
            be.close()

        overall_text = _strip_reasoning(raw_overall) or raw_overall
        overall_words = len(overall_text.split())
        # Compute total transcript words across all segments
        total_transcript_words = 0
        for seg_evs in segment_events:
            _, uc, _ = _build_prompt(seg_evs)
            total_transcript_words += len(uc.split())

        participants_line = (
            f"participants: {', '.join(participants)} · " if participants else ""
        )
        footer = (
            f"\n\n---\n"
            f"*Generated by Scarecrow · "
            f"model: {be.name} · "
            f"{total_transcript_words} words transcribed, "
            f"summarized in {overall_words} words · "
            f"{overall_tokens} tokens used · "
            f"{be.footer_info} · "
            f"{synthesis_seconds:.1f}s · "
            f"{participants_line}"
            f"{n_segments} segments synthesized*\n"
        )
        overall_text += footer

        summary_path = session_dir / "summary.md"
        summary_path.write_text(overall_text, encoding="utf-8")

        _progress(
            f"Summary complete — {overall_words} words, "
            f"{synthesis_seconds:.1f}s ({be.name})"
        )
        _sync_to_obsidian(summary_path, session_dir.name, obsidian_dir)
        return summary_path

    except Exception:
        log.exception("Multi-segment summarization failed")
        try:
            return _write_error_summary(session_dir, "Unexpected error — see logs")
        except Exception:
            return None
