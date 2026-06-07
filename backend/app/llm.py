"""OpenAI-compatible LLM client, prompt building, and streaming."""

import asyncio
import json
import re

import httpx
from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI

from . import config, events
from .config import (
    LLM_CONNECT_TIMEOUT,
    LLM_FIRST_TOKEN_TIMEOUT,
    LLM_INTERTOKEN_TIMEOUT,
    LLM_REASONING_EFFORT,
    LLM_TIMEOUT,
    OPENAI_API_KEY,
    OPENAI_BASE_URL,
    OPENAI_MODEL,
)

# Explicit timeouts so a slow or stalled endpoint can never hang the request.
# The transport `read` timeout is set to the (larger) first-token budget so it
# never trips before stream_completion's finer-grained first-token vs
# inter-token watchdog; LLM_TIMEOUT is the overall per-attempt ceiling.
# max_retries=0: a timeout should fail fast and predictably, not silently
# retry (the SDK default is 2) and blow past our wall-clock cap.
_client = AsyncOpenAI(
    base_url=OPENAI_BASE_URL,
    api_key=OPENAI_API_KEY,
    max_retries=0,
    timeout=httpx.Timeout(
        LLM_TIMEOUT, connect=LLM_CONNECT_TIMEOUT, read=LLM_FIRST_TOKEN_TIMEOUT
    ),
)


def _is_retryable(exc: BaseException) -> bool:
    """True if `exc` is a transient LLM failure worth retrying on another node.

    Retry: connection drops, timeouts (including our first-token/inter-token
    watchdog), and server-side 5xx / 429 — all common on an overloaded
    olla/Ollama cluster and likely to succeed when olla rebalances the retry
    elsewhere. Do NOT retry 4xx client errors (e.g. 400 BadRequestError): a
    malformed request won't fix itself. Order matters — check APIStatusError
    before the broad httpx.HTTPError branch.
    """
    if isinstance(exc, (APIConnectionError, APITimeoutError)):
        return True
    if isinstance(exc, asyncio.TimeoutError):  # our asyncio.timeout / wait_for
        return True
    if isinstance(exc, httpx.TimeoutException):
        return True
    if isinstance(exc, APIStatusError):  # has .status_code
        return exc.status_code == 429 or exc.status_code >= 500
    if isinstance(exc, httpx.HTTPError):  # transport-level (refused/reset)
        return True
    return False


def _delta_reasoning(delta) -> str | None:
    """Extract a reasoning-model's chain-of-thought token from a stream delta.

    Reasoning models (e.g. qwen3 via Ollama) stream their thinking in a
    non-standard field alongside an empty `content` — `reasoning` for Ollama,
    `reasoning_content` for some other shims. The OpenAI SDK keeps such unknown
    fields, exposed both as attributes and in `model_extra`; check both so the
    watchdog can treat active thinking as liveness rather than a stall.
    """
    value = getattr(delta, "reasoning", None) or getattr(
        delta, "reasoning_content", None
    )
    if value is None:
        extra = getattr(delta, "model_extra", None) or {}
        value = extra.get("reasoning") or extra.get("reasoning_content")
    return value


_SYSTEM_PROMPT = """You generate multiple-choice quizzes from provided source material.

Return JSON ONLY. No prose, no explanations, no markdown code fences.

The JSON must match this exact schema:
{
  "status": "ok",
  "message": "<short status message>",
  "data": {
    "questions": [
      {
        "question": "<the question text>",
        "options": [
          {"label": "A", "value": "<option text>"},
          {"label": "B", "value": "<option text>"}
        ],
        "answer": "<must exactly equal one option's value>"
      }
    ]
  }
}

Question rules:
- Each question must have between 2 and 6 options. Prefer 4 options.
- True/False questions are allowed as exactly 2 options.
- The "answer" field MUST exactly equal the "value" of one of that question's options.
- Every question must be answerable from the provided sources only. Do not invent facts.
- Use labels A, B, C, ... in order.
"""


def _extra_body() -> dict:
    """Extra request-body fields for Ollama: num_ctx and reasoning_effort.

    Read config values at call time (NUM_CTX is assigned during startup).
    Best-effort: Ollama's OpenAI-compatible shim (and olla in front of it) may
    ignore some of these — if so, the server uses its defaults. Each key is
    omitted when unset so non-Ollama endpoints stay safe.

    - num_ctx: allocate the model's full context window (set by the optimizer).
    - reasoning_effort: skip chain-of-thought for the JSON-extraction task. A
      reasoning model otherwise streams a long `reasoning` phase with empty
      `content`, wasting GPU time and starving the streaming watchdog (which only
      sees content). "none" makes the answer stream as content from token one.
    """
    extra: dict = {}
    if config.NUM_CTX is not None:
        extra["options"] = {"num_ctx": config.NUM_CTX}
    if LLM_REASONING_EFFORT:
        extra["reasoning_effort"] = LLM_REASONING_EFFORT
    if not extra:
        return {}
    return {"extra_body": extra}


def build_messages(chunks, user_instructions, num_questions):
    """Build the chat messages list from selected chunks and instructions."""
    source_blocks = []
    for i, chunk in enumerate(chunks, start=1):
        source_blocks.append(
            f"[Source {i} - {chunk['source']}]\n{chunk['text']}"
        )
    sources_text = "\n\n".join(source_blocks) if source_blocks else "(no sources)"

    user_parts = ["Use the following source material to write the quiz:", "", sources_text]
    if user_instructions and user_instructions.strip():
        user_parts += ["", "Additional user instructions:", user_instructions.strip()]
    user_parts += ["", f"Generate {num_questions} questions."]

    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": "\n".join(user_parts)},
    ]


async def stream_completion(messages, emit, *, label=None, on_progress=None):
    """Stream a chat completion, returning the accumulated content string.

    `label` tags the call so concurrent map calls can be told apart.
    `on_progress` is an optional async callback invoked (throttled) with the
    cumulative character count of the streamed content so far; the
    response_format fallback warning is still emitted via `emit`.
    """
    use_json_format = True
    content_parts = []

    async def _run(with_json_format):
        kwargs = {
            "model": OPENAI_MODEL,
            "messages": messages,
            "stream": True,
            **_extra_body(),
        }
        if with_json_format:
            kwargs["response_format"] = {"type": "json_object"}
        return await _client.chat.completions.create(**kwargs)

    # Overall wall-clock cap covering connection AND streaming; `async with
    # stream` closes the HTTP connection on exit (including timeout/cancel) so a
    # stalled endpoint can't leak it.
    async with asyncio.timeout(LLM_TIMEOUT):
        try:
            stream = await _run(use_json_format)
        except Exception as exc:  # noqa: BLE001 - endpoint may reject response_format
            if _is_retryable(exc):
                # A transient failure (connection/timeout/5xx/429) is not a
                # response_format rejection; let it propagate so the pipeline's
                # retry loop handles it. (CancelledError is a BaseException and
                # is not caught here, so cancellation still propagates cleanly.)
                raise
            await emit(
                events.log(
                    "response_format not supported, retrying without it",
                    level="warn",
                    error=str(exc),
                )
            )
            use_json_format = False
            stream = await _run(use_json_format)

        chunk_index = 0
        loop = asyncio.get_running_loop()
        last_activity = loop.time()
        reasoning_seen = False
        # `async with stream` closes the HTTP connection on exit — including
        # timeout and cancellation — which signals olla/Ollama to abort the
        # generation and free the runner. Keep this wrapper; don't swallow
        # CancelledError. No `await` runs between binding `stream` above and
        # entering the block, so a cancellation can't leak an open connection.
        async with stream:
            stream_iter = stream.__aiter__()
            while True:
                # The budget is measured against the last sign of REAL work —
                # either a content token or a reasoning token. A reasoning model
                # (qwen3) streams a long thinking phase with content="" and the
                # actual chain-of-thought in `reasoning`; that is the model
                # working, not a stall, so it must reset the watchdog. Only
                # truly empty / keep-alive frames (no content AND no reasoning)
                # let the budget run down. The first token gets a long leash
                # (cold load / reasoning legitimately exceeds a minute); once
                # work is flowing, gaps must stay short. LLM_TIMEOUT (the outer
                # asyncio.timeout) is the absolute cap on runaway reasoning.
                budget = LLM_FIRST_TOKEN_TIMEOUT if chunk_index == 0 else LLM_INTERTOKEN_TIMEOUT
                remaining = budget - (loop.time() - last_activity)
                if remaining <= 0:
                    # Stream is alive but silent past the budget -> treat as a
                    # stall (retryable) and let `async with` close the conn.
                    raise asyncio.TimeoutError(
                        f"no token within {budget}s (stream silent)"
                    )
                try:
                    event = await asyncio.wait_for(stream_iter.__anext__(), remaining)
                except StopAsyncIteration:
                    break
                if not event.choices:
                    continue
                delta = event.choices[0].delta
                if _delta_reasoning(delta):
                    # The model is thinking — alive, but no answer yet. Reset the
                    # watchdog and signal liveness once so the pipeline's
                    # first-content guard doesn't fail this as a wedged backend.
                    last_activity = loop.time()
                    if on_progress and not reasoning_seen:
                        reasoning_seen = True
                        await on_progress(0)
                piece = getattr(delta, "content", None)
                if piece:
                    content_parts.append(piece)
                    chunk_index += 1
                    last_activity = loop.time()
                    # Fire on the first token (proves liveness / first-token
                    # latency) then throttle so we don't spam the stream.
                    if on_progress and (chunk_index == 1 or chunk_index % 20 == 0):
                        await on_progress(sum(len(p) for p in content_parts))

    return "".join(content_parts)


async def repair_json(broken_text, emit):
    """One non-streaming call asking the model to return valid JSON only."""
    await emit(events.log("JSON repair attempted", level="warn"))

    messages = [
        {
            "role": "system",
            "content": (
                "You fix malformed JSON. Return ONLY valid JSON matching the "
                "quiz schema with keys status, message, and data.questions "
                "where each question has question, options (label/value), and "
                "answer matching one option value. No prose, no code fences."
            ),
        },
        {
            "role": "user",
            "content": "Fix this into valid JSON:\n\n" + broken_text,
        },
    ]

    kwargs = {"model": OPENAI_MODEL, "messages": messages, **_extra_body()}
    try:
        response = await _client.chat.completions.create(
            **kwargs, response_format={"type": "json_object"}
        )
    except Exception:
        response = await _client.chat.completions.create(**kwargs)

    return response.choices[0].message.content or ""


_SELECTOR_SYSTEM_PROMPT = """You curate a quiz from a candidate pool of questions.

You are given a numbered list of candidate questions, each tagged with its source.
Select the best set of at most {target_n} questions by INDEX.

Selection rules:
- Drop semantic duplicates and near-duplicates (questions testing the same fact),
  keeping only the single best phrasing.
- Balance coverage ACROSS sources — do not take most questions from one source.
- Prefer clear, unambiguous, factual questions.
- Return AT MOST {target_n} indices. Fewer is fine if the pool is small after dedupe.

Return JSON ONLY, no prose, no code fences, exactly this shape:
{{"keep": [<index>, <index>, ...]}}
The indices must come from the provided list."""


async def select_questions_llm(questions, target_n, emit, on_progress=None):
    """Reduce call: pick a deduped, source-balanced set of <= target_n questions.

    Returns the list of kept indices (ints) into `questions`, or None if the
    call/parse fails so the caller can fall back to a deterministic trim. The
    model only chooses indices — it never rewrites question text.

    Streams (via stream_completion) so first-token latency is visible: a
    reasoning model's silent "thinking" arrives as streamed tokens, and
    `on_progress` lets the caller show a heartbeat instead of a frozen UI. The
    caller bounds the whole call with REDUCE_DEADLINE_SECONDS.
    """
    lines = []
    for i, q in enumerate(questions):
        source = q.get("_source", "?")
        lines.append(f"[{i}] (source: {source}) {q.get('question', '')}")
    listing = "\n".join(lines)

    messages = [
        {
            "role": "system",
            "content": _SELECTOR_SYSTEM_PROMPT.format(target_n=target_n),
        },
        {
            "role": "user",
            "content": (
                f"Candidate questions ({len(questions)} total). Select at most "
                f"{target_n} by index:\n\n{listing}"
            ),
        },
    ]

    try:
        content = await stream_completion(
            messages, emit, label="reduce-select", on_progress=on_progress
        )
    except asyncio.CancelledError:
        raise  # let the caller's deadline (wait_for) cancel cleanly
    except Exception as exc:  # noqa: BLE001 - reduce failure is non-fatal
        await emit(
            events.log("LLM selection failed", level="warn", error=str(exc))
        )
        return None

    # Reasoning models (e.g. qwen3) may emit <think>...</think> and/or prose
    # around the JSON despite response_format, so strip those and extract the
    # JSON object rather than parsing the whole string strictly.
    text = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
    if text.startswith("```"):
        text = text.strip("`")
    match = re.search(r'\{.*"keep".*\}', text, re.DOTALL)
    if match:
        text = match.group(0)
    try:
        parsed = json.loads(text)
        raw_keep = parsed.get("keep", []) if isinstance(parsed, dict) else []
    except (ValueError, AttributeError):
        await emit(events.log("LLM selection parse failed", level="warn"))
        return None

    # Keep only valid, in-range, de-duplicated indices, preserving order.
    # Accept string-encoded ints (e.g. "3") but reject booleans.
    seen = set()
    keep = []
    for idx in raw_keep:
        if isinstance(idx, bool):
            continue
        try:
            idx = int(idx)
        except (TypeError, ValueError):
            continue
        if 0 <= idx < len(questions) and idx not in seen:
            seen.add(idx)
            keep.append(idx)

    if not keep:
        await emit(
            events.log("LLM selection returned no usable indices", level="warn")
        )
        return None
    return keep
