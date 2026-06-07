"""Pipeline orchestration: drives all steps and streams NDJSON events."""

import asyncio
import difflib
import json
import math
import re

from pydantic import ValidationError

from . import events, fetching, llm
from .chunking import chunk_sources, select_map_chunks
from .config import (
    DEDUPE_SIMILARITY_THRESHOLD,
    MAP_CONCURRENCY,
    MAP_DEADLINE_SECONDS,
    MAP_RETRIES,
    MAP_SUFFICIENCY,
    MAX_MAP_CHUNKS,
    REDUCE_DEADLINE_SECONDS,
    REDUCE_LLM_SELECTION_ENABLED,
    MIN_EXTRACTED_CHARS,
    MIN_QUESTIONS_PER_CHUNK,
    PER_CHUNK_QUESTION_BUFFER,
)
from .extraction import extract_text
from .models import GenerateRequest, QuizResult

_LABELS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def _strip_code_fences(text: str) -> str:
    """Strip reasoning <think> blocks and markdown code fences around JSON.

    Reasoning models (e.g. qwen3) may emit <think>...</think> before the JSON
    even with response_format set, which would otherwise break json.loads.
    """
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
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


def _normalize_question_text(text: str) -> str:
    """Lowercase and strip punctuation/extra whitespace for fuzzy comparison."""
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", "", text.lower())).strip()


def _dedupe_heuristic(questions: list, threshold: float) -> list:
    """Drop near-duplicate (and empty) questions via difflib ratio.

    Keeps the first occurrence of each near-duplicate cluster; preserves order.
    """
    kept: list = []
    kept_norms: list = []
    for q in questions:
        norm = _normalize_question_text(q.get("question", ""))
        if not norm:
            continue
        if any(
            difflib.SequenceMatcher(None, norm, kn).ratio() > threshold
            for kn in kept_norms
        ):
            continue
        kept.append(q)
        kept_norms.append(norm)
    return kept


def _balanced_trim(questions: list, n: int) -> list:
    """Round-robin across sources down to `n` questions (deterministic fallback)."""
    if len(questions) <= n:
        return list(questions)

    by_source: dict = {}
    order: list = []
    for q in questions:
        source = q.get("_source", "?")
        if source not in by_source:
            by_source[source] = []
            order.append(source)
        by_source[source].append(q)

    trimmed: list = []
    while len(trimmed) < n and any(by_source[s] for s in order):
        for source in order:
            bucket = by_source[source]
            if not bucket:
                continue
            trimmed.append(bucket.pop(0))
            if len(trimmed) >= n:
                break
    return trimmed


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
        await emit(events.progress("fetch", source=url, state="fetching"))
        ok, reason = fetching.validate_url(url)
        if not ok:
            await emit(
                events.log("Blocked URL", level="warn", url=url, reason=reason)
            )
            await emit(events.progress("fetch", source=url, state="blocked"))
            continue

        fetched = await fetching.fetch_url(url, emit)
        if not fetched:
            await emit(events.progress("fetch", source=url, state="blocked"))
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

        stripped = text.strip()
        if len(stripped) >= MIN_EXTRACTED_CHARS:
            sources.append({"label": url, "text": text})
            await emit(events.progress("fetch", source=url, state="ready"))
        elif stripped:
            await emit(
                events.log(
                    "Extracted content too short, skipping source",
                    level="warn",
                    url=url,
                    chars=len(stripped),
                    min=MIN_EXTRACTED_CHARS,
                )
            )
            await emit(events.progress("fetch", source=url, state="blocked"))
        else:
            await emit(
                events.log(
                    "No usable text extracted", level="warn", url=url
                )
            )
            await emit(events.progress("fetch", source=url, state="blocked"))

    # Pasted text becomes its own source.
    if req.text and req.text.strip():
        sources.append({"label": "pasted-text", "text": req.text})
        await emit(events.progress("fetch", source="pasted-text", state="ready"))

    # Step 3: bail out if nothing usable.
    if not sources:
        message = (
            "No usable content could be gathered — sources were blocked, "
            "empty, or too short."
        )
        await emit(events.log(message, level="error"))
        return _fail_result(message)

    # Step 4: chunk every source, then bound the fan-out to MAX_MAP_CHUNKS.
    await emit(events.log("Chunking started"))
    chunks = chunk_sources(sources)
    await emit(
        events.log(
            "Chunk count",
            count=len(chunks),
            sizes=[c["words"] for c in chunks],
        )
    )
    map_chunks = select_map_chunks(chunks, MAX_MAP_CHUNKS)
    if len(map_chunks) < len(chunks):
        await emit(
            events.log(
                "Capped chunks for fan-out",
                level="warn",
                selected=len(map_chunks),
                total=len(chunks),
                max=MAX_MAP_CHUNKS,
            )
        )

    num_questions = req.num_questions
    per_chunk_n = max(
        MIN_QUESTIONS_PER_CHUNK,
        math.ceil(num_questions / len(map_chunks)) + PER_CHUNK_QUESTION_BUFFER,
    )

    # Step 5: MAP — one concurrent LLM call per chunk, bounded by a semaphore.
    # olla load-balances the in-flight calls across the Ollama cluster.
    await emit(
        events.log(
            "Generating questions per chunk",
            chunks=len(map_chunks),
            per_chunk_n=per_chunk_n,
            concurrency=MAP_CONCURRENCY,
        )
    )
    sem = asyncio.Semaphore(MAP_CONCURRENCY)
    conn_failures = 0  # chunks lost to connection/timeout drops (vs bad output)

    # Shared live counters for the map-phase progress events. A single asyncio
    # loop runs every task, so mutating these from concurrent tasks is safe.
    map_total = len(map_chunks)
    done = 0
    running = 0
    failed = 0
    questions_total = 0

    async def emit_map_progress():
        await emit(
            events.progress(
                "map",
                total=map_total,
                done=done,
                running=running,
                failed=failed,
                questions=questions_total,
            )
        )

    # Emit the full roster up front so the graph renders every node instantly.
    await emit(
        events.progress(
            "map", total=map_total, done=0, running=0, failed=0, questions=0
        )
    )
    for i, c in enumerate(map_chunks):
        await emit(events.chunk(i, c["source"], "queued"))

    async def _generate_for_chunk(chunk: dict, chunk_id: int) -> list:
        """Generate, parse, and normalize questions for a single chunk.

        Returns a (possibly empty) list of clean question dicts tagged with the
        chunk's source. A failure is logged and yields [] — never fatal. A
        dropped connection is retried up to MAP_RETRIES times, since a busy
        local cluster intermittently refuses or closes streamed connections.
        """
        nonlocal conn_failures, done, running, failed, questions_total
        source = chunk["source"]
        messages = llm.build_messages([chunk], "", per_chunk_n)
        raw_text = None
        last_error = None
        async with sem:
            running += 1
            await emit(events.chunk(chunk_id, source, "running", chars=0))
            await emit_map_progress()
            for attempt in range(MAP_RETRIES + 1):
                try:
                    raw_text = await llm.stream_completion(
                        messages,
                        emit,
                        label=source,
                        on_progress=lambda c: emit(
                            events.chunk(chunk_id, source, "running", chars=c)
                        ),
                    )
                    break
                except Exception as exc:  # noqa: BLE001 - CancelledError is a BaseException, not caught
                    last_error = exc
                    if not llm._is_retryable(exc):
                        # Permanent (e.g. 4xx) — retrying won't help. Fall
                        # through to the raw_text-is-None failure path below.
                        break
                    if attempt < MAP_RETRIES:
                        await emit(
                            events.log(
                                "Chunk call failed, retrying",
                                level="warn",
                                source=source,
                                attempt=attempt + 1,
                                error=str(exc),
                            )
                        )
                        await emit(
                            events.chunk(
                                chunk_id,
                                source,
                                "retrying",
                                attempt=attempt + 1,
                                error=str(exc),
                            )
                        )
                        await asyncio.sleep(llm._retry_sleep(attempt))
        if raw_text is None:
            conn_failures += 1
            running -= 1
            failed += 1
            await emit(
                events.log(
                    "Chunk generation failed",
                    level="warn",
                    source=source,
                    error=str(last_error),
                )
            )
            await emit(
                events.chunk(chunk_id, source, "failed", error=str(last_error))
            )
            await emit_map_progress()
            return []

        try:
            parsed = json.loads(_strip_code_fences(raw_text))
        except (json.JSONDecodeError, ValueError):
            repaired = await llm.repair_json(raw_text, emit)
            try:
                parsed = json.loads(_strip_code_fences(repaired))
            except (json.JSONDecodeError, ValueError):
                running -= 1
                failed += 1
                await emit(
                    events.log(
                        "Chunk generation failed",
                        level="warn",
                        source=source,
                        error="output could not be parsed as JSON",
                    )
                )
                await emit(
                    events.chunk(
                        chunk_id, source, "failed", error="unparseable JSON"
                    )
                )
                await emit_map_progress()
                return []

        if not isinstance(parsed, dict):
            running -= 1
            failed += 1
            await emit(
                events.log(
                    "Chunk generation failed",
                    level="warn",
                    source=source,
                    error="output was not a JSON object",
                )
            )
            await emit(
                events.chunk(
                    chunk_id, source, "failed", error="output was not a JSON object"
                )
            )
            await emit_map_progress()
            return []

        warn_events = []
        normalized = _normalize(parsed, warn_events)
        for ev in warn_events:
            await emit(ev)

        questions = normalized["data"]["questions"]
        for q in questions:
            q["_source"] = source
        running -= 1
        done += 1
        questions_total += len(questions)
        await emit(events.chunk(chunk_id, source, "done", count=len(questions)))
        await emit_map_progress()
        return questions

    # Collect questions as chunks finish, but don't let one slow/stuck chunk
    # hold up the whole quiz: stop once we have enough (num_questions *
    # MAP_SUFFICIENCY) or the map deadline passes, then cancel the rest.
    threshold = max(num_questions, math.ceil(num_questions * MAP_SUFFICIENCY))
    loop = asyncio.get_running_loop()
    deadline = loop.time() + MAP_DEADLINE_SECONDS

    task_meta = {}
    for i, c in enumerate(map_chunks):
        task = asyncio.create_task(_generate_for_chunk(c, i))
        task_meta[task] = (i, c["source"])

    pool: list = []
    pending = set(task_meta)
    while pending:
        remaining = deadline - loop.time()
        if remaining <= 0:
            break  # map deadline reached
        completed, pending = await asyncio.wait(
            pending, timeout=remaining, return_when=asyncio.FIRST_COMPLETED
        )
        if not completed:
            break  # timed out waiting — deadline reached
        for task in completed:
            try:
                pool.extend(task.result())
            except Exception as exc:  # noqa: BLE001 - a crashed task is non-fatal
                await emit(
                    events.log(
                        "Chunk generation failed", level="warn", error=str(exc)
                    )
                )
        if len(pool) >= threshold:
            break

    if pending:
        reason = (
            "enough questions gathered"
            if len(pool) >= threshold
            else "map deadline reached"
        )
        await emit(
            events.log(
                "Stopping remaining chunks",
                reason=reason,
                cancelled=len(pending),
                questions=len(pool),
            )
        )
        for task in pending:
            task.cancel()
            cid, csrc = task_meta[task]
            await emit(events.chunk(cid, csrc, "skipped"))
        await asyncio.gather(*pending, return_exceptions=True)
        running = 0  # nothing is in flight once the stragglers are cancelled
        await emit(
            events.progress(
                "map",
                total=map_total,
                done=done,
                running=0,
                failed=failed,
                skipped=len(pending),
                questions=questions_total,
            )
        )

    # Fail only if NO chunk yielded any valid question.
    if not pool:
        if conn_failures >= len(map_chunks):
            message = (
                "The model endpoint dropped every request — it may be "
                "unreachable or overloaded. Please try again."
            )
        else:
            message = "No valid questions could be generated from any source."
        await emit(events.log(message, level="error"))
        return _fail_result(message)

    # Step 6: REDUCE — heuristic dedupe, then LLM selection, then fallback.
    before = len(pool)
    deduped = _dedupe_heuristic(pool, DEDUPE_SIMILARITY_THRESHOLD)
    await emit(
        events.log(
            "Heuristic dedupe", removed=before - len(deduped), remaining=len(deduped)
        )
    )
    await emit(
        events.progress(
            "reduce",
            step="dedupe",
            removed=before - len(deduped),
            remaining=len(deduped),
        )
    )

    selected_questions = None
    if len(deduped) <= num_questions:
        # Already at or under the target — no selection needed.
        selected_questions = deduped
    elif REDUCE_LLM_SELECTION_ENABLED:
        # A visible message (progress events carry no text) so the log doesn't
        # look frozen on "Heuristic dedupe" while the selector runs.
        await emit(
            events.log(
                "LLM selection started",
                candidates=len(deduped),
                target=num_questions,
                deadline_s=REDUCE_DEADLINE_SECONDS,
            )
        )
        await emit(events.progress("reduce", step="selection"))

        # Heartbeat: the streamed selector reports cumulative chars so the UI
        # shows it's alive (including a reasoning model's silent "thinking").
        async def _on_select_progress(chars):
            await emit(events.progress("reduce", step="selection", chars=chars))

        # Bound the selector so a slow/stuck reasoning model can't hang reduce;
        # on timeout we fall through to the deterministic balanced trim below.
        keep = None
        try:
            keep = await asyncio.wait_for(
                llm.select_questions_llm(
                    deduped, num_questions, emit, on_progress=_on_select_progress
                ),
                timeout=REDUCE_DEADLINE_SECONDS,
            )
        except Exception as exc:  # noqa: BLE001 - selector must never break reduce
            await emit(
                events.log(
                    "LLM selection timed out", level="warn", error=str(exc)
                )
            )
        if keep:
            selected_questions = [deduped[i] for i in keep][:num_questions]
            await emit(events.log("LLM selection", kept=len(selected_questions)))
            await emit(
                events.progress(
                    "reduce", step="selection", kept=len(selected_questions)
                )
            )
    # else: selector disabled but pool still over target -> balanced trim below.

    if not selected_questions:
        selected_questions = _balanced_trim(deduped, num_questions)
        await emit(
            events.log(
                "Reduce fallback used", level="warn", kept=len(selected_questions)
            )
        )
        await emit(
            events.progress("reduce", step="fallback", kept=len(selected_questions))
        )

    # Drop the internal source tag before building the output shape.
    for q in selected_questions:
        q.pop("_source", None)

    normalized_result = {
        "status": "ok",
        "message": f"Generated {len(selected_questions)} questions.",
        "data": {"questions": selected_questions},
    }

    # Step 7: validate with Pydantic.
    try:
        result = QuizResult(**normalized_result)
    except ValidationError as exc:
        await emit(
            events.log("Validation errors", level="error", errors=str(exc))
        )
        return _fail_result("Generated quiz failed validation.")

    # Step 8: finalize.
    await emit(
        events.log(
            "Final output normalized",
            level="success",
            questions=len(result.data.questions),
        )
    )
    await emit(events.log("Done", level="success"))
    await emit(events.progress("done"))
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
