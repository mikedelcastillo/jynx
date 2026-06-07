# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository rules

- **Keep all commits and PRs clean of AI attribution.** Never add Claude (or any AI) as a co-author, and never append a `Co-Authored-By: Claude ...` trailer or any "Generated with Claude Code" line. This applies to commit messages, PR titles, and PR bodies alike.
- **Use [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/) for every commit message and PR title.** Format: `<type>[optional scope]: <description>`, lower-case description, no trailing period. Common types: `feat`, `fix`, `refactor`, `docs`, `chore`, `test`, `build`, `ci`, `perf`. Breaking changes use a `!` (e.g. `feat!:`) or a `BREAKING CHANGE:` footer.

## What this is

Jynx is a V0 proof-of-concept that turns public webpages and/or pasted text into a playable multiple-choice quiz, streaming every backend step to the browser live. Two independently runnable services: a **Next.js** frontend (`frontend/`, port 4000) and a **FastAPI** backend (`backend/`, port 8000). The LLM is an **external OpenAI-compatible endpoint** — there is intentionally no database, vector store, cache, queue, or agent framework. Keep changes small and the pipeline readable; simplicity is a design goal (see `PLAN.md` for the full V0 contract).

## Commands

Backend (`backend/`):
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python -m app                                   # dev server — binds 0.0.0.0:8000, auto-reload (HOST/PORT/RELOAD env-overridable)
python scripts/eval.py                          # offline eval (needs a reachable OPENAI_BASE_URL)
python -c "from app.main import app"            # quick import smoke test
```

Frontend (`frontend/`):
```bash
npm install
cp .env.example .env.local
npm run dev                                     # dev server on :4000
npm run build                                   # production build (also the typecheck/lint gate)
```

Whole stack:
```bash
./dev.sh                                        # backend :8000 + frontend :4000 in one terminal (labeled logs, Ctrl-C stops both); sets up venv/node_modules/.env on first run
docker compose up --build                       # frontend :4000 + backend :8000
```

There is no unit-test suite; `scripts/eval.py` is the closest thing (an end-to-end pasted-text-only check against a live LLM), and `npm run build` is the frontend's correctness gate.

## Architecture

The contract between the two services is a single streamed endpoint: `POST /api/generate-quiz-stream` with body `{ urls: string[], text: string, num_questions: number }`, returning **newline-delimited JSON (NDJSON)** — one event object per line, not SSE framing. Events are `{type:"log", level, message, data}` lines, `{type:"chunk", id, source, state, ...}` per-chunk map-task lifecycle updates, `{type:"progress", phase, ...}` pipeline stage snapshots, and a single terminal `{type:"final", data:<QuizResult>}` (still exactly one). The frontend consumes `chunk`/`progress` to render a live visualization. Both ends must stay in sync on this format; the backend emits it from `app/events.py`, the frontend parses it in `lib/stream.ts`.

### Backend pipeline (`backend/app/`)

`pipeline.run_pipeline()` is the heart of the system and runs the fixed flow. It uses an **`asyncio.Queue` + background task** pattern: the work coroutine calls an `emit` callback that puts NDJSON lines on the queue, while the async generator drains the queue and yields to the `StreamingResponse`. This is what lets every helper (fetch, extract, LLM) report progress without being generators themselves. The flow is a **parallel per-chunk fan-out (map-reduce)**:

1. `fetching.py` — `validate_url()` is **SSRF-critical**: http/https only, resolves hosts via `getaddrinfo`, and rejects loopback/private/link-local/reserved/unspecified IPs (IPv4 + IPv6). `fetch_url()` enforces timeout, body-size cap, one retry, and **re-validates the host after redirects**. Treat any change here as security-sensitive.
2. `extraction.py` — trafilatura first, BeautifulSoup fallback (drops script/style/nav/header/footer/aside), whitespace-cleaned and length-capped.
3. `chunking.py` — paragraph-aware packing to ~`TARGET_CHUNK_WORDS` via `chunk_sources`, then `select_map_chunks(chunks, MAX_MAP_CHUNKS)` picks up to `MAX_MAP_CHUNKS` chunks, source-balanced (pasted text first, then round-robin across sources) so the map phase isn't just the start of the first source.
4. **MAP** (`llm.py` — `AsyncOpenAI` against `OPENAI_BASE_URL`) — each selected chunk becomes its **own** LLM call, grounded in that chunk only and asking for a small per-chunk quota (derived from the requested `num_questions`). All calls run concurrently via `asyncio.gather` bounded by an `asyncio.Semaphore(MAP_CONCURRENCY)` (asyncio, not threads/processes — the calls are network-I/O-bound; in practice an `olla` load balancer fans them across multiple Ollama servers). Each call streams, requests `response_format={"type":"json_object"}` but **falls back to a plain call if the endpoint rejects it**, parses → `repair_json()` once on failure → normalizes → validates (see below). A chunk that raises or times out is logged at `warn` and **skipped — it is no longer fatal**; questions that survive are tagged with their source. The quiz task is pure JSON extraction, so the pipeline asks the endpoint to **skip chain-of-thought** via `LLM_REASONING_EFFORT`/`reasoning_effort` (the only lever Ollama honors) — otherwise a reasoning model like qwen3 streams a long `reasoning` phase with empty `content`, burning GPU before the answer. The streaming call is wrapped in a **liveness watchdog** (`stream_completion`): the first token gets a long leash (`LLM_FIRST_TOKEN_TIMEOUT`, for cold model load / reasoning) and subsequent gaps a short one (`LLM_INTERTOKEN_TIMEOUT`); a stall raises a retryable `TimeoutError` and the `async with stream` closes the connection so olla/Ollama frees the runner. If an endpoint ignores `reasoning_effort` and streams thinking anyway, those `reasoning`/`reasoning_content` frames **count as liveness** (`_delta_reasoning`) so an actively-thinking model isn't misread as a stall.
5. **REDUCE** — collect all validated questions, then: (1) heuristic near-duplicate removal via `difflib.SequenceMatcher` over `DEDUPE_SIMILARITY_THRESHOLD`; (2) a single LLM "selector" call (`select_questions_llm`) that returns a deduped, source-balanced set of at most `num_questions` question **indices** — it never rewrites text, only selects — then reconstructs from the already-validated objects; (3) fallback: if the selector fails or returns empty, a deterministic round-robin `_balanced_trim` across sources down to `num_questions`.
6. Normalization + validation (applied per-chunk in the map phase) — model output is never trusted: parse → repair once on failure → normalize (wrap bare `{questions}`, relabel options A/B/C…, drop questions whose answer doesn't match an option or whose option count isn't 2–6) → validate against the Pydantic models in `models.py` (`QuizResult`/`QuizData`/`Question`/`Option`; `Question` enforces 2–6 options and answer-matches-an-option). The pipeline **always emits exactly one `final` event with the normalized shape, even on exceptions**, and now returns a `status:"fail"` QuizResult **only when no chunk yielded any valid question** (strictly more resilient than the old single-call flow).

The streaming contract still ends in exactly one `{type:"final",...}`; alongside the `{type:"log",...}` lines (per-chunk start/result, dedupe, selection, fallback), the pipeline also emits per-chunk `{type:"chunk",...}` lifecycle events (`queued`/`running`/`retrying`/`done`/`failed`, with live `chars`/`count`/`attempt`/`error`) and stage `{type:"progress",...}` snapshots (`phase` ∈ `fetch`/`chunk`/`map`/`reduce`/`done`) for the live UI.

The requested question count comes from the request's `num_questions` field (default `DEFAULT_NUM_QUESTIONS`, clamped server-side to `MIN_NUM_QUESTIONS`–`MAX_NUM_QUESTIONS`). All tunable limits and the `OPENAI_*` env vars live in `config.py` — that file is the only place `OPENAI_*` defaults should appear; never hardcode them elsewhere. New knobs there include `MAP_CONCURRENCY` (default 3, one generation per Ollama server) and `MAX_MAP_CHUNKS` (default 40, caps total parallel calls/cost), plus `DEFAULT/MIN/MAX_NUM_QUESTIONS`, `MIN_QUESTIONS_PER_CHUNK`, `PER_CHUNK_QUESTION_BUFFER`, and `DEDUPE_SIMILARITY_THRESHOLD`. The **streaming-watchdog knobs** (tuned for a slow, flaky local Ollama cluster behind olla) are the per-attempt timeouts `LLM_CONNECT_TIMEOUT`/`LLM_FIRST_TOKEN_TIMEOUT`/`LLM_INTERTOKEN_TIMEOUT`/`LLM_TIMEOUT` and the map-phase backstop `MAP_DEADLINE_SECONDS` (240). All are env-overridable. `backend/scripts/` holds deterministic, network-free regression tests for this logic — `test_watchdog.py` (stalls abort at the first-token budget) and `test_reasoning_liveness.py` (reasoning frames count as liveness).

### Frontend (`frontend/`)

App Router, single client page (`app/page.tsx`) with a three-state machine: `input → loading → result`. The frontend **never calls the LLM or fetches target webpages** — it only talks to the backend. `lib/stream.ts` `streamQuiz()` is an async generator that POSTs and yields parsed NDJSON events (handling partial-line buffering); `page.tsx` builds view-model state from the `chunk`/`progress` events and, in the loading view, renders a **live radial pipeline graph** (`components/PipelineGraph.tsx`, built on **React Flow / `@xyflow/react`** — core node at center → source nodes → chunk nodes fanning out → converging to a final node), with a bottom-docked collapsible raw-log drawer (`components/LogDrawer.tsx`) for the raw NDJSON trail, then switches to the result view on the `final` event. `@xyflow/react` is a deliberate, intentional dependency exception in this otherwise-minimal repo. Quiz play (`components/Quiz.tsx`) is entirely client-side. The app is **single-origin**: `lib/stream.ts` fetches a relative `/api/...` path, and `next.config.mjs` `rewrites()` proxies `/api/*` to `BACKEND_ORIGIN` (server-side env, default `http://localhost:8000`; the backend service name under Compose). This means no baked-in IP and no CORS — the app works from any device that can reach the frontend. `rewrites()` resolves when next.config loads (startup for `next dev`, build time for the standalone image — hence `BACKEND_ORIGIN` is a Docker build ARG). `compress: false` keeps the proxied NDJSON stream from being buffered. `NEXT_PUBLIC_API_BASE_URL` is an optional override to bypass the proxy and call a backend directly.
