# Jynx — V0 Plan

Jynx is a webpage/text-to-quiz RAG-style proof of concept. Users enter public webpage URLs and/or paste extra text/instructions and choose how many questions they want. The backend fetches the pages, extracts and chunks text in memory, then fans out one LLM call per selected chunk (map) and reduces the combined questions down to the requested count via dedupe + an LLM selector (reduce), returning a playable multiple-choice quiz. All LLM calls go through an OpenAI-compatible API. Every backend action is streamed live to the client.

This document is the V0 contract. Keep everything simple — this is a POC, not a product.

## 1. Current project structure summary

The project directory was empty at planning time (no source files, no git). Toolchain available: Node v24, npm 11, Python 3.12, Docker 29. We start from scratch.

## 2. Proposed V0 architecture

Two services, isolated by directory:

```
jynx/
  PLAN.md
  README.md                 # root usage docs (local + Docker)
  docker-compose.yml        # starts frontend + backend only
  .gitignore
  backend/                  # FastAPI Python service
    app/
      __init__.py
      config.py             # env loading (OPENAI_*, limits)
      models.py             # Pydantic models for quiz output
      fetching.py           # URL validation + safe fetch (SSRF guards)
      extraction.py         # HTML -> clean text
      chunking.py           # in-memory chunking
      llm.py                # OpenAI-compatible client + prompt building
      pipeline.py           # orchestration + event generation
      events.py             # event helpers / SSE formatting
      main.py               # FastAPI app + streaming endpoint
    scripts/
      eval.py               # tiny pasted-text-only eval
    requirements.txt
    Dockerfile
    .env.example
  frontend/                 # Next.js (App Router) SSR + UI
    app/
      layout.tsx
      page.tsx              # single-page app, all states
      globals.css
    components/
      UrlInput.tsx
      LogConsole.tsx
      Quiz.tsx
      RawJsonPanel.tsx
    lib/
      stream.ts             # parse streamed events from backend
      types.ts              # shared TS types
    package.json
    next.config.mjs
    tsconfig.json
    Dockerfile
    .env.example
```

- **Frontend (Next.js, port 4000):** UI only. Never calls the LLM, never fetches target webpages. Posts to the backend stream endpoint and renders streamed logs + final quiz.
- **Backend (FastAPI, port 8000):** does all ingestion, fetching, chunking, LLM orchestration, validation, normalization, and streaming.

Frontend talks to backend at `NEXT_PUBLIC_API_BASE_URL` (default `http://localhost:8000`). Backend talks to an external OpenAI-compatible endpoint at `OPENAI_BASE_URL`.

## 3. Frontend responsibilities

- Render the single-page UI with three states: input, loading, result.
- Manage URL list (Enter to add, X to remove) and textarea state.
- POST `{ urls, text, num_questions }` to backend stream endpoint and read the streamed body.
- Parse newline-delimited JSON events; from the `chunk`/`progress` events render a live radial pipeline graph (React Flow / `@xyflow/react`) during loading, with a bottom-docked collapsible log drawer for the raw NDJSON trail; show errors visibly.
- On `final` event, store normalized output and transition to result screen.
- Result screen: Retry, Close (back to input), View sample results (raw JSON modal/panel).
- Play the quiz fully client-side: multiple choice, prev/next, changeable answers, score = correct/total. No timer, no ranks, no modes.

## 4. Python backend responsibilities

- Accept POST `{ urls, text, num_questions }` (`num_questions` clamped server-side to 1–30).
- Validate URLs (scheme + SSRF blocklist). Stream blocked URLs with reason.
- Safely fetch each allowed URL (timeout, size cap, redirect re-validation).
- Extract readable text (trafilatura primary, BeautifulSoup fallback), clean, cap length.
- Combine webpage + pasted text, chunk in memory with source labels, select up to `MAX_MAP_CHUNKS` source-balanced chunks for the map phase.
- **Map:** fan out one OpenAI-compatible chat completion per selected chunk (streaming), each grounded in that chunk only and asking for a small per-chunk quota; run them concurrently bounded by `MAP_CONCURRENCY`. Parse/repair/normalize/validate each result independently; a chunk that fails is logged and skipped, not fatal.
- **Reduce:** combine all validated questions (tagged by source), drop near-duplicates heuristically, then run a single LLM selector call that picks at most `num_questions` deduped, source-balanced questions by index (it selects, never rewrites); fall back to a deterministic round-robin trim if the selector fails.
- Stream every step as JSON events (`log` lines plus per-chunk `chunk` lifecycle events and stage `progress` snapshots for the live UI); emit a single `final` event with the normalized output. Always return the normalized shape; only `fail` when no chunk produced any valid question.

Direct Python only. No LangChain / LangGraph / agent framework.

## 5. API contract

`POST /api/generate-quiz-stream`

Request body:
```json
{ "urls": ["https://example.com/article"], "text": "extra pasted info or instructions", "num_questions": 10 }
```
`num_questions` is optional (default 10) and clamped server-side to 1–30.

Response: `text/event-stream` style streaming body. The transport is **newline-delimited JSON** (one JSON object per line, `\n` terminated) for simplicity and reliability with `fetch` streaming. (We use NDJSON rather than formal SSE `data:` framing — it is simpler to parse on both ends and works with plain streaming fetch.) `Content-Type: application/x-ndjson`. Each line is one event object — `log`, `chunk`, or `progress` along the way, with exactly one terminal `type: "final"` last.

Health: `GET /api/health` -> `{ "status": "ok" }`.

CORS: allow the frontend origin (`*` is fine for V0).

## 6. Streaming event format

JSON objects, one per line. Four event types: `log`, `chunk`, `progress`, and a single terminal `final`.

Log event:
```json
{ "type": "log", "level": "info", "message": "Fetching URL", "data": { "url": "https://example.com" } }
```
`level` ∈ `info | warn | error | success`. `data` optional.

Chunk event (one per-chunk map-task's lifecycle; live — `chars` ticks up while running):
```json
{ "type": "chunk", "id": 0, "source": "https://example.com", "state": "running", "chars": 120, "count": 0, "attempt": 1, "error": null }
```
`state` ∈ `queued | running | retrying | done | failed`. `chars`/`count`/`attempt`/`error` are optional and depend on state.

Progress event (pipeline stage snapshot):
```json
{ "type": "progress", "phase": "map", "total": 8, "done": 3, "running": 2, "failed": 0, "questions": 11 }
```
`phase` ∈ `fetch | chunk | map | reduce | done`. `fetch` carries `source` + `state` (`fetching`/`ready`/`blocked`); `map` carries `total`/`done`/`running`/`failed`/`questions`; `reduce` carries `step` (`dedupe`/`selection`/`fallback`) + counts.

Final event:
```json
{ "type": "final", "data": { "status": "ok", "message": "all ok", "data": { "questions": [] } } }
```

The frontend treats `type: "log"` as a console line, builds its live radial pipeline graph from `chunk`/`progress` events, and treats `type: "final"` as completion (exactly one).

## 7. Ingestion pipeline

1. `request received` — echo counts of urls/text and requested `num_questions`.
2. For each URL: `validating URLs` -> validate -> `blocked URL` (with reason) or `fetching URL`.
3. Fetch: `fetch started`, `HTTP status`, `content type`, `raw HTML size`, `fetch completed`. On error: `retry attempt` (one retry), `retry failed`/`retry succeeded`. On final failure stream error and continue.
4. Extract: `extraction started`, `extracted text size`, `extraction fallback used` if BS4 path taken.
5. Clean: `text cleaning started`, `text cleaning completed`.
6. Combine + chunk: `chunking started`, `chunk count`, `approximate chunk sizes`, then `selected map chunks` (up to `MAX_MAP_CHUNKS`, source-balanced).
7. **Map** (per selected chunk, concurrent, bounded by `MAP_CONCURRENCY`): `chunk LLM call started`, optional `LLM token/chunk received`, then per-chunk parse/repair/normalize/validate; `chunk produced N questions` or, on failure/timeout, `chunk skipped` (warn). Not fatal. Each chunk also emits `chunk` lifecycle events (`queued`→`running`/`retrying`→`done`/`failed`) and the phase emits `progress` snapshots for the live UI.
8. **Reduce:** `collected M questions`, `dedupe removed K`, `selector call started`/`selector picked N` (or `selector fallback (balanced trim)` if it fails/empties), `final output normalized`, `done`. Reduce steps also emit `progress` snapshots (`step` ∈ `dedupe`/`selection`/`fallback`).
9. `final` event with normalized output (exactly one).

If no usable content was gathered (all URLs failed and no pasted text) or no chunk produced any valid question, emit a `fail` final.

## 8. In-memory chunking strategy

- Combine all extracted webpage texts + pasted text. Keep a `source` label per chunk (e.g. URL or `pasted-text`).
- Split each source on blank lines (paragraph-aware); greedily pack paragraphs into chunks targeting ~900 words (≈ 5400 chars), hard cap per chunk. No overlap for V0.
- Avoid tiny chunks: merge fragments below ~150 words into the previous chunk.
- Map selection (`select_map_chunks`): pick up to `MAX_MAP_CHUNKS` chunks, source-balanced — pasted text first, then round-robin across sources so the map phase isn't just the start of the first source. Each selected chunk becomes its own LLM call, so this caps total parallel calls/cost rather than packing one big context.
- Stream selected chunk count and approximate per-chunk sizes.

## 9. LLM prompting strategy

- **Map prompt (per chunk):** System prompt — "You generate quizzes. Return JSON only, no prose, no markdown fences." Describe the exact output schema and the question rules (2–6 options, answer must equal one option value, prefer 4 options, True/False as 2 options, answerable from the chunk). User prompt: the single source chunk (with its source label), plus any user instructions from pasted text, plus a request for a small per-chunk quota derived from `num_questions` (at least `MIN_QUESTIONS_PER_CHUNK`, with a `PER_CHUNK_QUESTION_BUFFER` so the reduce step has surplus to pick from). All chunk calls run concurrently, bounded by `MAP_CONCURRENCY`.
- **Reduce / selector prompt:** a single call given the deduped pool of already-validated questions (with source tags); it returns at most `num_questions` question indices, deduped and source-balanced — it selects, never rewrites. On failure/empty, fall back to a deterministic round-robin trim.
- Use `response_format={"type":"json_object"}` when the endpoint supports it; otherwise rely on the prompt. Stream tokens as they arrive.

## 10. JSON validation strategy

Pydantic models validate the normalized output:
- `Option { label: str, value: str }`
- `Question { question: str, options: List[Option] (2..6), answer: str }` — validator: `answer` must exactly match one `option.value`.
- `QuizData { questions: List[Question] }`
- `QuizResult { status: "ok"|"fail", message: str, data: QuizData }`

Flow: parse JSON -> if parse fails, one repair pass (ask LLM to fix to valid JSON, or simple brace/fence stripping) -> normalize (ensure labels A,B,C…; coerce shapes; drop invalid questions) -> validate -> on success `status:"ok"`, on failure `status:"fail"` with message. Stream each step. Never trust raw model output.

## 11. Local development instructions

See README. Summary:
- Backend: `cd backend && python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt && cp .env.example .env && uvicorn app.main:app --reload --port 8000`.
- Frontend: `cd frontend && npm install && cp .env.example .env.local && npm run dev` (port 4000).
- Eval: `cd backend && python scripts/eval.py`.

## 12. Docker deployment plan

- `backend/Dockerfile`: python:3.12-slim, install requirements, run uvicorn on 8000.
- `frontend/Dockerfile`: node:24-alpine multi-stage, `next build`, run `next start` on 4000.
- `docker-compose.yml`: two services (`backend`, `frontend`). Backend reads `OPENAI_*` from env/`.env`. Frontend gets `NEXT_PUBLIC_API_BASE_URL`. For browser-side calls the frontend uses `http://localhost:8000` (host-mapped). No DB/Redis/vector/Ollama containers — the OpenAI-compatible endpoint is external (`OPENAI_BASE_URL`, reachable via `host.docker.internal`).

## 13. Implementation checklist

- [ ] PLAN.md (this file)
- [ ] Backend: config, models, fetching (SSRF-safe), extraction, chunking, llm, pipeline, events, main
- [ ] Backend streaming endpoint emits all events + final
- [ ] Backend `.env.example`, `requirements.txt`, `Dockerfile`
- [ ] Backend eval script (pasted text only)
- [ ] Frontend: input / loading / result states, quiz UI, raw JSON panel
- [ ] Frontend stream parsing, `.env.example`, `Dockerfile`
- [ ] Root `docker-compose.yml`, `README.md`, `.gitignore`
