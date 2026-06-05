# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository rules

- **Keep all commits and PRs clean of AI attribution.** Never add Claude (or any AI) as a co-author, and never append a `Co-Authored-By: Claude ...` trailer or any "Generated with Claude Code" line. This applies to commit messages, PR titles, and PR bodies alike.
- **Use [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/) for every commit message and PR title.** Format: `<type>[optional scope]: <description>`, lower-case description, no trailing period. Common types: `feat`, `fix`, `refactor`, `docs`, `chore`, `test`, `build`, `ci`, `perf`. Breaking changes use a `!` (e.g. `feat!:`) or a `BREAKING CHANGE:` footer.

## What this is

Jynx is a V0 proof-of-concept that turns public webpages and/or pasted text into a playable multiple-choice quiz, streaming every backend step to the browser live. Two independently runnable services: a **Next.js** frontend (`frontend/`, port 3000) and a **FastAPI** backend (`backend/`, port 8000). The LLM is an **external OpenAI-compatible endpoint** — there is intentionally no database, vector store, cache, queue, or agent framework. Keep changes small and the pipeline readable; simplicity is a design goal (see `PLAN.md` for the full V0 contract).

## Commands

Backend (`backend/`):
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload --port 8000      # dev server
python scripts/eval.py                          # offline eval (needs a reachable OPENAI_BASE_URL)
python -c "from app.main import app"            # quick import smoke test
```

Frontend (`frontend/`):
```bash
npm install
cp .env.example .env.local
npm run dev                                     # dev server on :3000
npm run build                                   # production build (also the typecheck/lint gate)
```

Whole stack:
```bash
docker compose up --build                       # frontend :3000 + backend :8000
```

There is no unit-test suite; `scripts/eval.py` is the closest thing (an end-to-end pasted-text-only check against a live LLM), and `npm run build` is the frontend's correctness gate.

## Architecture

The contract between the two services is a single streamed endpoint: `POST /api/generate-quiz-stream` with body `{ urls: string[], text: string }`, returning **newline-delimited JSON (NDJSON)** — one event object per line, not SSE framing. Events are either `{type:"log", level, message, data}` or a single terminal `{type:"final", data:<QuizResult>}`. Both ends must stay in sync on this format; the backend emits it from `app/events.py`, the frontend parses it in `lib/stream.ts`.

### Backend pipeline (`backend/app/`)

`pipeline.run_pipeline()` is the heart of the system and runs the fixed flow. It uses an **`asyncio.Queue` + background task** pattern: the work coroutine calls an `emit` callback that puts NDJSON lines on the queue, while the async generator drains the queue and yields to the `StreamingResponse`. This is what lets every helper (fetch, extract, LLM) report progress without being generators themselves. The flow:

1. `fetching.py` — `validate_url()` is **SSRF-critical**: http/https only, resolves hosts via `getaddrinfo`, and rejects loopback/private/link-local/reserved/unspecified IPs (IPv4 + IPv6). `fetch_url()` enforces timeout, body-size cap, one retry, and **re-validates the host after redirects**. Treat any change here as security-sensitive.
2. `extraction.py` — trafilatura first, BeautifulSoup fallback (drops script/style/nav/header/footer/aside), whitespace-cleaned and length-capped.
3. `chunking.py` — paragraph-aware packing to ~`TARGET_CHUNK_WORDS`, then `select_chunks()` does round-robin-across-sources selection under `MAX_CONTEXT_CHARS` (so output isn't just the start of the first source; pasted text is always included).
4. `llm.py` — `AsyncOpenAI` against `OPENAI_BASE_URL`. Streams completions; requests `response_format={"type":"json_object"}` but **falls back to a plain call if the endpoint rejects it**. `repair_json()` is the one-shot fix path.
5. Normalization + validation — model output is never trusted: parse → repair once on failure → normalize (wrap bare `{questions}`, relabel options A/B/C…, drop questions whose answer doesn't match an option or whose option count isn't 2–6) → validate against the Pydantic models in `models.py` (`QuizResult`/`QuizData`/`Question`/`Option`; `Question` enforces 2–6 options and answer-matches-an-option). The pipeline **always emits exactly one `final` event with the normalized shape, even on exceptions** (a `status:"fail"` QuizResult).

All tunable limits and the `OPENAI_*` env vars live in `config.py` — that file is the only place `OPENAI_*` defaults should appear; never hardcode them elsewhere.

### Frontend (`frontend/`)

App Router, single client page (`app/page.tsx`) with a three-state machine: `input → loading → result`. The frontend **never calls the LLM or fetches target webpages** — it only talks to the backend. `lib/stream.ts` `streamQuiz()` is an async generator that POSTs and yields parsed NDJSON events (handling partial-line buffering); `page.tsx` appends log events to the live `LogConsole` and switches to the result view on the `final` event. Quiz play (`components/Quiz.tsx`) is entirely client-side. `NEXT_PUBLIC_API_BASE_URL` is **baked at build time** (it's a `NEXT_PUBLIC_` var), so the Docker image takes it as a build ARG.
