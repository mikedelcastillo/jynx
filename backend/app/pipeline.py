"""Pipeline orchestration: drives all steps and streams NDJSON events."""

import asyncio
import json
import re

from pydantic import ValidationError

from . import events, fetching, llm
from .chunking import chunk_sources, select_chunks
from .config import NUM_QUESTIONS
from .extraction import extract_text
from .models import GenerateRequest, QuizResult

_LABELS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def _strip_code_fences(text: str) -> str:
    """Remove markdown code fences if the model wrapped JSON in them."""
    text = text.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    if fence:
        return fence.group(1).strip()
    return text


def _fail_result(message: str) -> dict:
    """Build a normalized fail QuizResult dict."""
    return QuizResult(
        status="fail", message=message, data={"questions": []}
    ).model_dump()


def _normalize(raw: dict, emit_list) -> dict:
    """Normalize a parsed dict into the QuizResult shape.

    Relabels options, drops invalid questions, and wraps bare shapes.
    `emit_list` collects warning events to be emitted by the caller.
    """
    # Wrap a bare {"questions": [...]} shape.
    if "data" not in raw and "questions" in raw:
        raw = {
            "status": raw.get("status", "ok"),
            "message": raw.get("message", "Generated quiz"),
            "data": {"questions": raw["questions"]},
        }

    status = raw.get("status", "ok")
    message = raw.get("message", "Generated quiz")
    data = raw.get("data") or {}
    questions = data.get("questions") or []

    clean_questions = []
    for idx, q in enumerate(questions):
        if not isinstance(q, dict):
            emit_list.append(
                events.log("Dropping non-object question", level="warn", index=idx)
            )
            continue
        options = q.get("options") or []
        if not isinstance(options, list) or not (2 <= len(options) <= 6):
            emit_list.append(
                events.log(
                    "Dropping question with invalid option count",
                    level="warn",
                    index=idx,
                    count=len(options) if isinstance(options, list) else 0,
                )
            )
            continue

        # Relabel options A, B, C, ... while preserving values.
        relabeled = []
        valid = True
        for i, opt in enumerate(options):
            if isinstance(opt, dict):
                value = opt.get("value", opt.get("label", ""))
            else:
                value = str(opt)
            if value == "":
                valid = False
                break
            relabeled.append({"label": _LABELS[i], "value": value})

        if not valid:
            emit_list.append(
                events.log(
                    "Dropping question with empty option value",
                    level="warn",
                    index=idx,
                )
            )
            continue

        answer = q.get("answer", "")
        values = [o["value"] for o in relabeled]
        # If the answer is a label (e.g. "A"), map it to the option value.
        if answer not in values and answer in _LABELS[: len(relabeled)]:
            answer = relabeled[_LABELS.index(answer)]["value"]

        if answer not in values:
            emit_list.append(
                events.log(
                    "Dropping question whose answer matches no option",
                    level="warn",
                    index=idx,
                )
            )
            continue

        clean_questions.append(
            {
                "question": q.get("question", ""),
                "options": relabeled,
                "answer": answer,
            }
        )

    return {
        "status": status if status in ("ok", "fail") else "ok",
        "message": message,
        "data": {"questions": clean_questions},
    }


async def _work(req: GenerateRequest, emit):
    """Run the full pipeline, emitting events. Returns the final QuizResult dict."""
    await emit(
        events.log(
            "Request received",
            level="info",
            url_count=len(req.urls),
            text_chars=len(req.text or ""),
        )
    )

    sources = []

    # Step 2: process each URL.
    for url in req.urls:
        await emit(events.log("Validating URL", url=url))
        ok, reason = fetching.validate_url(url)
        if not ok:
            await emit(
                events.log("Blocked URL", level="warn", url=url, reason=reason)
            )
            continue

        fetched = await fetching.fetch_url(url, emit)
        if not fetched:
            continue

        await emit(events.log("Extraction started", url=url))
        text, used_fallback = extract_text(fetched["html"], url)
        if used_fallback:
            await emit(
                events.log("Extraction fallback used", level="warn", url=url)
            )
        await emit(events.log("Extracted text size", url=url, chars=len(text)))

        await emit(events.log("Text cleaning started", url=url))
        # Cleaning happens inside extract_text; this marks the step boundary.
        await emit(events.log("Text cleaning completed", url=url, chars=len(text)))

        if text.strip():
            sources.append({"label": url, "text": text})
        else:
            await emit(
                events.log(
                    "No usable text extracted", level="warn", url=url
                )
            )

    # Pasted text becomes its own source.
    if req.text and req.text.strip():
        sources.append({"label": "pasted-text", "text": req.text})

    # Step 3: bail out if nothing usable.
    if not sources:
        await emit(
            events.log(
                "No usable content could be gathered.", level="error"
            )
        )
        return _fail_result("No usable content could be gathered.")

    # Step 4: chunking.
    await emit(events.log("Chunking started"))
    chunks = chunk_sources(sources)
    await emit(
        events.log(
            "Chunk count",
            count=len(chunks),
            sizes=[c["words"] for c in chunks],
        )
    )
    selected = select_chunks(chunks)
    await emit(
        events.log(
            "Chunks selected",
            count=len(selected),
            chars=sum(c["chars"] for c in selected),
        )
    )

    # Step 5: prompt preparation.
    await emit(events.log("LLM prompt preparation started"))
    context_chars = sum(c["chars"] for c in selected)
    await emit(events.log("Approximate context length", chars=context_chars))
    messages = llm.build_messages(selected, req.text, NUM_QUESTIONS)

    # Step 6: LLM call.
    await emit(events.log("LLM call started"))
    raw_text = await llm.stream_completion(messages, emit)
    await emit(
        events.log("LLM call completed", level="success", chars=len(raw_text))
    )

    # Step 7: JSON parse (+ repair).
    await emit(events.log("JSON parse started"))
    parsed = None
    try:
        parsed = json.loads(_strip_code_fences(raw_text))
    except (json.JSONDecodeError, ValueError):
        await emit(events.log("JSON parse failed", level="warn"))
        repaired = await llm.repair_json(raw_text, emit)
        try:
            parsed = json.loads(_strip_code_fences(repaired))
        except (json.JSONDecodeError, ValueError):
            await emit(
                events.log("JSON parse failed after repair", level="error")
            )
            return _fail_result("Model output could not be parsed as JSON.")

    if not isinstance(parsed, dict):
        await emit(
            events.log("Parsed JSON was not an object", level="error")
        )
        return _fail_result("Model output was not a JSON object.")

    # Step 8: normalize.
    await emit(events.log("Validation started"))
    warn_events = []
    normalized = _normalize(parsed, warn_events)
    for ev in warn_events:
        await emit(ev)

    if not normalized["data"]["questions"]:
        await emit(
            events.log("No valid questions remained after normalization", level="error")
        )
        return _fail_result("No valid questions could be generated.")

    # Step 9: validate with Pydantic.
    try:
        result = QuizResult(**normalized)
    except ValidationError as exc:
        await emit(
            events.log("Validation errors", level="error", errors=str(exc))
        )
        return _fail_result("Generated quiz failed validation.")

    # Step 10: finalize.
    await emit(
        events.log(
            "Final output normalized",
            level="success",
            questions=len(result.data.questions),
        )
    )
    await emit(events.log("Done", level="success"))
    return result.model_dump()


async def run_pipeline(req: GenerateRequest):
    """Async generator yielding NDJSON strings for the whole pipeline.

    Always yields exactly one final event, even on unexpected errors.
    """
    queue: asyncio.Queue = asyncio.Queue()

    async def emit(ev):
        await queue.put(events.to_ndjson(ev))

    async def work():
        try:
            result = await _work(req, emit)
        except Exception as exc:  # noqa: BLE001 - always produce a final
            await queue.put(
                events.to_ndjson(
                    events.log("Pipeline error", level="error", error=str(exc))
                )
            )
            result = _fail_result(f"Pipeline failed: {exc}")
        await queue.put(events.to_ndjson(events.final(result)))
        await queue.put(None)

    task = asyncio.create_task(work())
    try:
        while True:
            item = await queue.get()
            if item is None:
                break
            yield item
    finally:
        if not task.done():
            task.cancel()
