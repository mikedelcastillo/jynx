"""OpenAI-compatible LLM client, prompt building, and streaming."""

from openai import AsyncOpenAI

from . import events
from .config import OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_MODEL

_client = AsyncOpenAI(base_url=OPENAI_BASE_URL, api_key=OPENAI_API_KEY)

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


async def stream_completion(messages, emit):
    """Stream a chat completion, returning the accumulated content string."""
    use_json_format = True
    content_parts = []

    async def _run(with_json_format):
        kwargs = {
            "model": OPENAI_MODEL,
            "messages": messages,
            "stream": True,
        }
        if with_json_format:
            kwargs["response_format"] = {"type": "json_object"}
        return await _client.chat.completions.create(**kwargs)

    try:
        stream = await _run(use_json_format)
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
    async for event in stream:
        if not event.choices:
            continue
        delta = event.choices[0].delta
        piece = getattr(delta, "content", None)
        if piece:
            content_parts.append(piece)
            chunk_index += 1
            # Throttle token logs so we don't spam the stream.
            if chunk_index % 20 == 0:
                await emit(
                    events.log(
                        "LLM chunk received",
                        chunks=chunk_index,
                        chars=sum(len(p) for p in content_parts),
                    )
                )

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

    kwargs = {"model": OPENAI_MODEL, "messages": messages}
    try:
        response = await _client.chat.completions.create(
            **kwargs, response_format={"type": "json_object"}
        )
    except Exception:
        response = await _client.chat.completions.create(**kwargs)

    return response.choices[0].message.content or ""
