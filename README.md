# Jynx

**Turn any webpage or block of text into a playable quiz — and watch the whole pipeline happen live.**

Jynx is a retrieval-augmented quiz generator. Paste in some text and/or a few public URLs, and it fetches the pages, extracts the readable content, chunks it in memory, and asks an LLM to write a multiple-choice quiz grounded in that material. Every step — fetching, extraction, chunking, the LLM call, JSON validation — is **streamed to the browser in real time**, so you can see exactly what the system is doing and why.

> This is a deliberately scoped **V0 proof-of-concept**: no database, no vector store, no agent framework. The goal is a clean, end-to-end RAG-style pipeline that's easy to read and reason about.

---

## What this project demonstrates

- **End-to-end LLM application** — ingestion → extraction → in-memory chunking → prompt construction → generation → structured validation → interactive UI, with no black-box framework in between.
- **Real-time streaming UX** — the backend emits structured NDJSON events and the frontend renders a live "Quizzifying…" console, so the pipeline is observable instead of a spinner.
- **Security-conscious fetching** — the URL fetcher is hardened against SSRF: scheme allow-listing plus DNS-resolution checks that block `localhost`, loopback, private, link-local, and reserved IP ranges (IPv4 **and** IPv6), with re-validation after redirects.
- **Trustworthy model output** — LLM responses are never trusted raw. Output is parsed, **repaired on failure**, normalized, and validated against **Pydantic** schemas (answer must match a real option, option counts bounded, etc.) before it ever reaches the UI.
- **Clean service boundaries** — a Next.js frontend that does zero LLM/network-to-the-internet work, and a FastAPI backend that owns the entire pipeline. Each is independently runnable and Dockerized.
- **Pragmatic engineering** — built to a fixed, well-understood flow, so it intentionally avoids LangChain/agents. Simplicity is the feature.

## Tech stack

| Layer      | Choice                                                                 |
| ---------- | --------------------------------------------------------------------- |
| Frontend   | **Next.js** (App Router, TypeScript, React) — streaming fetch UI       |
| Backend    | **FastAPI** + **httpx** + **trafilatura/BeautifulSoup** + **Pydantic** |
| LLM access | Any **OpenAI-compatible** chat-completions endpoint (Ollama / Olla / vLLM / OpenAI) |
| Transport  | **NDJSON** event stream over a single streamed HTTP response           |
| Packaging  | **Docker** + Docker Compose (two services, external LLM)               |

## Architecture

```
Browser
   │  (HTTP / streamed NDJSON events)
   ▼
Frontend — Next.js (App Router, TypeScript)        :3000
   │  POST /api/generate-quiz-stream
   ▼
Backend — FastAPI (Python)                          :8000
   │  validate URLs → fetch → extract → chunk → prompt
   │  → generate → parse/repair → validate → normalize
   ▼
External OpenAI-compatible endpoint (Olla / Ollama)  e.g. http://localhost:40114/v1
```

The LLM endpoint is **external and configurable** — Jynx bundles no LLM server, database, cache, or vector store.

### How a request flows

1. **Validate** every URL (scheme + SSRF/IP checks) and stream any blocked URL with its reason.
2. **Fetch** allowed pages with a timeout, response-size cap, one retry, and post-redirect re-validation.
3. **Extract** readable text (trafilatura first, BeautifulSoup fallback), clean whitespace, cap length.
4. **Chunk** the combined webpage + pasted text into paragraph-aware chunks, then select a diverse set under a context budget.
5. **Generate** a quiz via the LLM, streaming tokens as they arrive.
6. **Validate & normalize** the result with Pydantic — parse, repair once if needed, relabel options, drop malformed questions — and stream a single final event with the clean quiz.

---

## Quick start

You need **Node 24+**, **Python 3.12+**, and a reachable **OpenAI-compatible endpoint** (e.g. Ollama, or Olla load-balancing Ollama, exposing the OpenAI API at `http://localhost:40114/v1`). Docker is optional.

### Run with Docker (one command)

```bash
docker compose up --build
```

Frontend → http://localhost:3000 · Backend → http://localhost:8000. To reach an LLM endpoint on the **host** from inside the containers, set `OPENAI_BASE_URL=http://host.docker.internal:40114/v1`.

### Run locally (two terminals)

**Backend**
```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # then edit as needed
uvicorn app.main:app --reload --port 8000
```

**Frontend**
```bash
cd frontend
npm install
cp .env.example .env.local      # then edit as needed
npm run dev                     # open http://localhost:3000
```

## Configuring the LLM endpoint

The backend talks to any OpenAI-compatible **chat-completions** endpoint (defaults shown):

| Variable          | Default                     | Description                                       |
| ----------------- | --------------------------- | ------------------------------------------------- |
| `OPENAI_BASE_URL` | `http://localhost:40114/v1` | Base URL of the OpenAI-compatible API.            |
| `OPENAI_API_KEY`  | `ollama`                    | API key (Ollama/Olla accept any non-empty value). |
| `OPENAI_MODEL`    | `qwen2.5:14b`               | Model name to request.                            |

## Trying it out

Open http://localhost:3000 and:

- **Paste text only** — drop in a couple of paragraphs, add no URLs, and hit **Submit**.
- **Use a public URL** — type a URL, press **Enter** to add it as a chip, then **Submit**.
- **Watch the stream** — the live console shows fetching, extraction, chunking, the LLM call, and validation. When it finishes you get a playable multiple-choice quiz (prev/next, changeable answers, score) plus **Retry**, **Close**, and **View sample results** (raw JSON).

## Eval script

A small offline check exercises generation against pasted text (needs a reachable `OPENAI_BASE_URL`):

```bash
cd backend && python scripts/eval.py
```

It verifies the LLM output parses, validates against the schema, has a non-empty question list, gives each question **2–6** options, and that each answer matches one of its options.

## Project structure

```
jynx/
├── backend/                # FastAPI service (:8000)
│   ├── app/                # main, config, models, fetching, extraction, chunking, llm, pipeline, events
│   ├── scripts/            # eval.py
│   ├── Dockerfile
│   ├── requirements.txt
│   └── .env.example
├── frontend/               # Next.js App Router app (:3000)
│   ├── app/                # layout, page (input/loading/result states), styles
│   ├── components/         # UrlInput, LogConsole, Quiz, RawJsonPanel
│   ├── lib/                # NDJSON streaming client + shared types
│   ├── Dockerfile
│   ├── package.json
│   └── .env.example
├── docker-compose.yml      # Two-service stack (frontend + backend)
├── PLAN.md                 # Architecture & design notes
└── README.md
```

## Design notes

The full V0 design — API contract, streaming event format, ingestion pipeline, chunking strategy, prompting, and validation rules — lives in [`PLAN.md`](./PLAN.md).
