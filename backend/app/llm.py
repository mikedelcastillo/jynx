"""OpenAI-compatible LLM client, prompt building, and streaming."""

import asyncio
import json
import re

import httpx
from openai import APIConnectionError, AsyncOpenAI

from . import config, events
from .config import (
    CRAWL_RELEVANCE_TEMPERATURE,
    LLM_CONNECT_TIMEOUT,
    LLM_READ_TIMEOUT,
    LLM_REASONING_EFFORT,
    LLM_TIMEOUT,
    OPENAI_API_KEY,
    OPENAI_BASE_URL,
    OPENAI_MODEL,
)

# Explicit timeouts so a slow or stalled endpoint can never hang the request.
# read caps the gap between streamed tokens (the first-token wait especially);
# LLM_TIMEOUT is the overall per-call ceiling.
# max_retries=0: a timeout should fail fast and predictably, not silently
# retry (the SDK default is 2) and blow past our wall-clock cap.
_client = AsyncOpenAI(
    base_url=OPENAI_BASE_URL,
    api_key=OPENAI_API_KEY,
    max_retries=0,
    timeout=httpx.Timeout(
        LLM_TIMEOUT, connect=LLM_CONNECT_TIMEOUT, read=LLM_READ_TIMEOUT
    ),
)

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
      `content`, wasting GPU time. "none" makes the answer stream from token one.
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


async def stream_completion(messages, emit, *, label=None, on_progress=None, temperature=None):
    """Stream a chat completion, returning the accumulated content string.

    `label` tags the call so concurrent map calls can be told apart.
    `on_progress` is an optional async callback invoked (throttled) with the
    cumulative character count of the streamed content so far; the
    response_format fallback warning is still emitted via `emit`.
    `temperature`, when set, is sent through — selection/ranking calls use a low
    value for consistent, decisive picks; omitted leaves the server default.
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
        if temperature is not None:
            kwargs["temperature"] = temperature
        if with_json_format:
            kwargs["response_format"] = {"type": "json_object"}
        return await _client.chat.completions.create(**kwargs)

    # Overall wall-clock cap covering connection AND streaming; `async with
    # stream` closes the HTTP connection on exit (including timeout/cancel) so a
    # stalled endpoint can't leak it.
    async with asyncio.timeout(LLM_TIMEOUT):
        try:
            stream = await _run(use_json_format)
        except (APIConnectionError, httpx.TimeoutException):
            # A timeout/connection failure is not a response_format rejection;
            # let it propagate so the pipeline reports it cleanly.
            raise
        except Exception as exc:  # noqa: BLE001 - endpoint may reject response_format
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
        async with stream:
            async for event in stream:
                if not event.choices:
                    continue
                delta = event.choices[0].delta
                piece = getattr(delta, "content", None)
                if piece:
                    content_parts.append(piece)
                    chunk_index += 1
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


_SELECTOR_SYSTEM_PROMPT_TOPIC = """You curate a quiz from a candidate pool of \
questions gathered by crawling pages about a specific topic.

You are given the QUIZ TOPIC and a numbered list of candidate questions, each
tagged with its source. Select at most {target_n} questions by INDEX and RANK
them by relevance to the topic.

Selection rules:
- Rank by how directly each question tests knowledge of the QUIZ TOPIC. Return
  the indices ordered MOST RELEVANT FIRST.
- Drop semantic duplicates and near-duplicates (questions testing the same fact),
  keeping only the single best phrasing.
- Prefer clear, unambiguous, factual questions.
- Drop questions that are off-topic relative to the QUIZ TOPIC.
- Return AT MOST {target_n} indices. Fewer is fine if few are on-topic after dedupe.

Return JSON ONLY, no prose, no code fences, exactly this shape:
{{"keep": [<index>, <index>, ...]}}
Order the indices most-relevant-first. They must come from the provided list."""


async def select_questions_llm(questions, target_n, emit, *, seed_topic=None, on_progress=None):
    """Reduce call: pick a deduped set of <= target_n questions by index.

    When `seed_topic` is given, the set is RANKED by relevance to that topic and
    returned most-relevant-first; otherwise it is deduped and source-balanced.
    Returns the list of kept indices (ints) into `questions`, or None if the
    call/parse fails so the caller can fall back to a deterministic trim. The
    model only chooses indices — it never rewrites question text.

    Streams (via stream_completion) so first-token latency is visible: a
    reasoning model's silent "thinking" arrives as streamed tokens, and
    `on_progress` lets the caller show a heartbeat instead of a frozen UI. The
    caller bounds the whole call with a reduce deadline.
    """
    lines = []
    for i, q in enumerate(questions):
        source = q.get("_source", "?")
        lines.append(f"[{i}] (source: {source}) {q.get('question', '')}")
    listing = "\n".join(lines)

    if seed_topic:
        system_content = _SELECTOR_SYSTEM_PROMPT_TOPIC.format(target_n=target_n)
        user_content = (
            f"QUIZ TOPIC:\n{seed_topic}\n\n"
            f"Candidate questions ({len(questions)} total). Select and rank at "
            f"most {target_n} by index:\n\n{listing}"
        )
    else:
        system_content = _SELECTOR_SYSTEM_PROMPT.format(target_n=target_n)
        user_content = (
            f"Candidate questions ({len(questions)} total). Select at most "
            f"{target_n} by index:\n\n{listing}"
        )

    messages = [
        {"role": "system", "content": system_content},
        {"role": "user", "content": user_content},
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


_LINK_SELECTOR_SYSTEM_PROMPT = """You select which links are worth following to \
deepen a quiz's source material.

You are given:
1. The QUIZ TOPIC (what the quiz is fundamentally about).
2. A summary of the CURRENT PAGE you are on.
3. A numbered list of candidate links (URL + visible link text) found on that page.

Select AT MOST {max_n} links by INDEX that are most likely to contain \
substantive content ON THE QUIZ TOPIC.

Selection rules:
- Stay on-topic: judge each link against the QUIZ TOPIC, not just the current
  page, so the crawl does not drift into unrelated subjects.
- Prefer links to substantive articles/content over navigation, login, share,
  legal, tag, category, or index/listing links.
- It is fine to follow off-site links if they are clearly on-topic.
- Select the relevant links; return an empty list only if none are relevant.

Return JSON ONLY, no prose, no code fences, exactly this shape:
{{"keep": [<index>, <index>, ...]}}
The indices must come from the provided list."""


async def select_relevant_links_llm(
    seed_topic, page_summary, candidates, max_n, emit, *, label=None, on_progress=None
):
    """Pick up to max_n on-topic links by index from `candidates`.

    `candidates` is a list of {"url", "text"}. Grounds the judgment on BOTH
    seed_topic (drift resistance across levels) and page_summary (local
    relevance). Returns a list of kept indices into `candidates`, or None on any
    failure so the caller skips crawling that page — link selection is never
    fatal. The model only chooses indices; it never rewrites or invents URLs.

    Streams (via stream_completion) so a slow reasoning model's progress is
    visible; the caller bounds the whole call with CRAWL_RELEVANCE_DEADLINE_SECONDS.
    """
    lines = [
        f"[{i}] {c.get('text') or '(no text)'} -> {c['url']}"
        for i, c in enumerate(candidates)
    ]
    listing = "\n".join(lines)

    messages = [
        {
            "role": "system",
            "content": _LINK_SELECTOR_SYSTEM_PROMPT.format(max_n=max_n),
        },
        {
            "role": "user",
            "content": (
                f"QUIZ TOPIC:\n{seed_topic}\n\n"
                f"CURRENT PAGE SUMMARY:\n{page_summary}\n\n"
                f"Candidate links ({len(candidates)} total). "
                f"Select at most {max_n} by index:\n\n{listing}"
            ),
        },
    ]

    try:
        content = await stream_completion(
            messages,
            emit,
            label=label or "crawl-select",
            on_progress=on_progress,
            temperature=CRAWL_RELEVANCE_TEMPERATURE,
        )
    except asyncio.CancelledError:
        raise  # let the caller's deadline (wait_for) cancel cleanly
    except Exception as exc:  # noqa: BLE001 - crawl selection is non-fatal
        await emit(
            events.log("Link selection failed", level="warn", error=str(exc))
        )
        return None

    # Same robust parse as select_questions_llm: strip <think>/fences and pull
    # out the {"keep": [...]} object rather than parsing the whole string.
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
        await emit(events.log("Link selection parse failed", level="warn"))
        return None

    seen = set()
    keep = []
    for idx in raw_keep:
        if isinstance(idx, bool):
            continue
        try:
            idx = int(idx)
        except (TypeError, ValueError):
            continue
        if 0 <= idx < len(candidates) and idx not in seen:
            seen.add(idx)
            keep.append(idx)

    return keep[:max_n] or None
