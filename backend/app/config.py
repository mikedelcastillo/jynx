"""Configuration loaded from environment variables."""

import os
from pathlib import Path

from dotenv import load_dotenv

# Load the shared project-root .env first (one file configures the whole
# project), then a backend-local .env if present. Neither overrides variables
# already set in the real environment (e.g. those provided by Docker Compose).
_ROOT_ENV = Path(__file__).resolve().parents[2] / ".env"
load_dotenv(_ROOT_ENV)
load_dotenv()

# OpenAI-compatible endpoint settings (the only place these defaults live).
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "http://localhost:40114/v1")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "ollama")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "qwen2.5:14b")

# Server bind. Defaults to 0.0.0.0 so the backend is reachable from other
# devices/containers, not just localhost. Override with HOST/PORT env vars.
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8000"))

# Limits / tuning constants.
REQUEST_TIMEOUT = 10  # seconds
MAX_HTML_BYTES = 3_000_000
MAX_EXTRACTED_CHARS = 40_000
MIN_EXTRACTED_CHARS = 200  # below this, a fetched source is treated as unusable
TARGET_CHUNK_WORDS = 900  # each chunk is one parallel LLM call's source material

# Quiz size. The user picks a count in the UI; the request is clamped to this
# range (see models.GenerateRequest) so an out-of-range value never 422s.
DEFAULT_NUM_QUESTIONS = 10
MIN_NUM_QUESTIONS = 1
MAX_NUM_QUESTIONS = 30

# Parallel fan-out (map) tuning. Each ~TARGET_CHUNK_WORDS chunk becomes its own
# LLM call; calls run concurrently and `olla` load-balances them across the
# Ollama cluster. MAP_CONCURRENCY bounds in-flight calls (default 3 = one per
# server); MAX_MAP_CHUNKS caps the total number of calls (and thus token cost).
MIN_QUESTIONS_PER_CHUNK = 2  # floor so each chunk yields a useful candidate set
PER_CHUNK_QUESTION_BUFFER = 1  # over-ask per chunk so the reduce step has room
MAP_CONCURRENCY = int(os.getenv("MAP_CONCURRENCY", "3"))
MAX_MAP_CHUNKS = int(os.getenv("MAX_MAP_CHUNKS", "40"))
# Transient connection drops are common on a busy local cluster (olla/Ollama
# intermittently refusing or closing streamed connections). Retry a dropped
# per-chunk call this many times before giving up on that chunk.
MAP_RETRIES = int(os.getenv("MAP_RETRIES", "1"))

# Don't let one slow/stuck chunk hold up the whole quiz. The map phase stops as
# soon as it has gathered enough questions (num_questions * MAP_SUFFICIENCY) and
# cancels any still-running chunks; MAP_DEADLINE_SECONDS is a hard backstop that
# cancels stragglers even if that threshold isn't reached. The reduce step then
# dedupes/selects from whatever was collected.
MAP_SUFFICIENCY = float(os.getenv("MAP_SUFFICIENCY", "1.5"))
MAP_DEADLINE_SECONDS = float(os.getenv("MAP_DEADLINE_SECONDS", "90"))

# The reduce LLM "selector" is a best-effort nicety: it asks the model to pick
# the best N-of-M questions by index. A slow reasoning model (e.g. qwen3) never
# finishes the pick in time and always falls back to the deterministic balanced
# trim — so this is gated and tightly bounded. When disabled, reduce goes
# straight to the instant balanced trim (no LLM call, no wait).
REDUCE_LLM_SELECTION_ENABLED = os.getenv(
    "REDUCE_LLM_SELECTION_ENABLED", "true"
).lower() in ("1", "true", "yes", "on")
# Bound the selector so reduce never hangs: past this, fall back to the instant
# deterministic balanced trim. Kept short — the call now streams, so progress is
# visible while it runs, and a model that can't pick fast should yield quickly.
REDUCE_DEADLINE_SECONDS = float(os.getenv("REDUCE_DEADLINE_SECONDS", "8"))

# difflib SequenceMatcher ratio above which two questions are near-duplicates.
DEDUPE_SIMILARITY_THRESHOLD = 0.9

# --- Context-window chunk-size optimization (see app/optimize.py) ----------
# At startup the backend learns the model's context window and resizes each
# chunk to fill it (fewer, larger LLM calls), and sends num_ctx so Ollama
# allocates the full window instead of truncating to its ~4096 default.
#
# Master feature flag. When off, TARGET_CHUNK_WORDS stays static (the value
# above) and no num_ctx is sent. Parsed like RELOAD.
CHUNK_OPTIMIZATION_ENABLED = os.getenv(
    "CHUNK_OPTIMIZATION_ENABLED", "true"
).lower() in ("1", "true", "yes", "on")

# Context window (tokens). When set explicitly it is BOTH the probe fallback
# AND a hard cap on the probed value (so a model's trained max can't OOM the
# node); when unset, the probe wins and 8192 is only the fallback. Read the raw
# env once so we can tell "explicitly set" from "default".
_MODEL_CTX_RAW = os.getenv("MODEL_CONTEXT_WINDOW")
MODEL_CONTEXT_WINDOW = int(_MODEL_CTX_RAW) if _MODEL_CTX_RAW else 8192
MODEL_CONTEXT_WINDOW_IS_EXPLICIT = _MODEL_CTX_RAW is not None

OUTPUT_RESERVE_TOKENS = int(os.getenv("OUTPUT_RESERVE_TOKENS", "1500"))  # reserved for generated JSON
PROMPT_OVERHEAD_TOKENS = int(os.getenv("PROMPT_OVERHEAD_TOKENS", "600"))  # system prompt + scaffolding
TOKENS_PER_WORD = float(os.getenv("TOKENS_PER_WORD", "1.3"))  # heuristic; no tokenizer dependency
CONTEXT_SAFETY_FRACTION = float(os.getenv("CONTEXT_SAFETY_FRACTION", "0.9"))
MIN_CHUNK_WORDS = int(os.getenv("MIN_CHUNK_WORDS", "300"))  # clamp floor (> chunking's 150 merge floor)
MAX_CHUNK_WORDS = int(os.getenv("MAX_CHUNK_WORDS", "8000"))  # clamp ceiling (caps blast radius)
PROBE_TIMEOUT = float(os.getenv("PROBE_TIMEOUT", "3"))  # seconds for the /api/show probe

# Cache of probed context windows per model, so reload/model-switch never
# re-probes (a JSON map {model: window}). Local artifact, gitignored.
PROBE_CACHE_PATH = os.getenv(
    "PROBE_CACHE_PATH",
    str(Path(__file__).resolve().parents[1] / ".cache" / "context_windows.json"),
)

# Per-call num_ctx, set by the optimizer at startup. None = don't send it.
NUM_CTX = None

# A descriptive User-Agent is what most sites (e.g. Wikipedia) expect; the httpx
# default UA is frequently blocked with a 403.
USER_AGENT = os.getenv(
    "FETCH_USER_AGENT",
    "JynxQuizBot/0.1 (+https://github.com/; educational quiz generator)",
)

# LLM call timeouts (seconds). REQUEST_TIMEOUT above is for fetching only.
LLM_CONNECT_TIMEOUT = 10  # establishing the connection
LLM_READ_TIMEOUT = 60  # max gap waiting for the next/first streamed token
LLM_TIMEOUT = 120  # overall wall-clock cap for one generation

# Reasoning-model control. Models like qwen3 emit a long chain-of-thought (in a
# separate `reasoning` stream field, with content="" the whole time) before the
# answer — wasting GPU time on a task that needs none. The quiz task is JSON
# extraction, so we ask the endpoint to skip thinking. `reasoning_effort` is the
# only lever Ollama/olla actually honor here (think:false,
# chat_template_kwargs.enable_thinking, and /no_think are all ignored). Sent as a
# top-level request field via extra_body; set to "" to omit it (e.g. for
# endpoints that reject the field).
LLM_REASONING_EFFORT = os.getenv("LLM_REASONING_EFFORT", "none")
