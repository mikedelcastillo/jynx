"""Startup context-window probe and chunk-size optimization.

Runs once at server startup (before requests are served, via the FastAPI
lifespan in app/main.py). It learns the selected model's context window, sizes
each chunk to fill it (fewer, larger LLM calls), and sets the per-call num_ctx
so Ollama actually allocates that window instead of truncating to its default.

Successful probes are cached per-model on disk so reloads and model-switching
never re-probe. Everything here is best-effort and never raises: a failed probe
falls back to a configured value, and a disabled flag is a no-op — the server
must always start.
"""

import json
import logging
import math
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import httpx

from . import config

logger = logging.getLogger("app.optimize")

# Bump whenever the probe/parse semantics change so old cached entries are
# discarded and every model is re-probed once on the next start.
CACHE_VERSION = 1


def _probe_url() -> str:
    """Derive the native Ollama base URL from OPENAI_BASE_URL (strip a /v1).

    e.g. http://host:40114/v1 -> http://host:40114. For olla-prefixed paths
    like /olla/openai/v1 the native /api/show route won't exist; that simply
    makes the probe fail and fall back, which is handled by the caller.
    """
    parts = urlsplit(config.OPENAI_BASE_URL)
    path = parts.path.rstrip("/")
    if path.endswith("/v1"):
        path = path[: -len("/v1")]
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


def _load_cache() -> dict:
    """Read the probe cache; return a fresh empty cache on any problem.

    A missing file, malformed JSON, or a version mismatch all yield an empty
    cache (a CACHE_VERSION bump thus silently invalidates every entry).
    """
    try:
        with open(config.PROBE_CACHE_PATH, "r", encoding="utf-8") as f:
            cache = json.load(f)
        if isinstance(cache, dict) and cache.get("version") == CACHE_VERSION:
            models = cache.get("models")
            if isinstance(models, dict):
                return {"version": CACHE_VERSION, "models": models}
    except (OSError, ValueError):
        pass
    return {"version": CACHE_VERSION, "models": {}}


def _save_cache(cache: dict) -> None:
    """Write the probe cache, creating parent dirs. Never raises."""
    try:
        path = Path(config.PROBE_CACHE_PATH)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=2)
    except OSError as exc:
        logger.warning("could not write probe cache %s: %s", config.PROBE_CACHE_PATH, exc)


async def _probe_context_window() -> int:
    """Query Ollama's /api/show for the model's context length. May raise."""
    url = _probe_url() + "/api/show"
    async with httpx.AsyncClient(timeout=config.PROBE_TIMEOUT) as client:
        resp = await client.post(url, json={"model": config.OPENAI_MODEL})
        resp.raise_for_status()
        model_info = resp.json().get("model_info") or {}
    # The key is architecture-specific, e.g. "qwen2.context_length".
    lengths = [
        int(v)
        for k, v in model_info.items()
        if k.endswith("context_length") and v is not None
    ]
    if not lengths:
        raise ValueError("no context_length in model_info")
    return max(lengths)


async def resolve_context_window() -> tuple[int, str]:
    """Resolve the model's RAW (uncapped) context window in tokens.

    Cache-first: a cached probe means no network call (keeps reloads instant).
    Otherwise probe and cache the result. On any failure, fall back to the
    configured value without caching it. Returns (tokens, source) where source
    is one of "cache" | "probe" | "env" | "default". Never raises.
    """
    model = config.OPENAI_MODEL
    cache = _load_cache()
    cached = cache["models"].get(model)
    if isinstance(cached, int) and cached > 0:
        return cached, "cache"

    try:
        value = await _probe_context_window()
    except Exception as exc:  # noqa: BLE001 - any probe failure falls back
        source = "env" if config.MODEL_CONTEXT_WINDOW_IS_EXPLICIT else "default"
        logger.warning(
            "context-window probe failed (%s); falling back to %s=%d",
            exc, source, config.MODEL_CONTEXT_WINDOW,
        )
        return config.MODEL_CONTEXT_WINDOW, source

    cache["models"][model] = value
    _save_cache(cache)
    return value, "probe"


def compute_chunk_words(context_window: int) -> int:
    """Size a chunk (in words) to fill the window, leaving room for prompt+output.

    Pure function. Clamped into [MIN_CHUNK_WORDS, MAX_CHUNK_WORDS].
    """
    budget = (
        context_window * config.CONTEXT_SAFETY_FRACTION
        - config.OUTPUT_RESERVE_TOKENS
        - config.PROMPT_OVERHEAD_TOKENS
    )
    if budget <= 0:
        logger.warning(
            "context window %d too small for reserves; clamping chunk size to %d words",
            context_window, config.MIN_CHUNK_WORDS,
        )
        return config.MIN_CHUNK_WORDS
    words = math.floor(budget / config.TOKENS_PER_WORD)
    return max(config.MIN_CHUNK_WORDS, min(config.MAX_CHUNK_WORDS, words))


async def optimize_startup() -> dict:
    """Probe/resolve the context window, resize chunks, set num_ctx, and log.

    Flag-gated and never raises so the server always starts. Mutates
    config.TARGET_CHUNK_WORDS and config.NUM_CTX in place; both are read at call
    time by chunking.py and llm.py respectively.
    """
    if not config.CHUNK_OPTIMIZATION_ENABLED:
        logger.info(
            "chunk-size optimization disabled; using static TARGET_CHUNK_WORDS=%d",
            config.TARGET_CHUNK_WORDS,
        )
        return {"enabled": False, "chunk_words": config.TARGET_CHUNK_WORDS}

    raw, source = await resolve_context_window()
    window = (
        min(raw, config.MODEL_CONTEXT_WINDOW)
        if config.MODEL_CONTEXT_WINDOW_IS_EXPLICIT
        else raw
    )
    words = compute_chunk_words(window)
    config.TARGET_CHUNK_WORDS = words
    config.NUM_CTX = window

    logger.info(
        "Context-window optimization: model=%s window=%d (%s) -> chunk_words=%d, "
        "num_ctx=%d [reserve=%d overhead=%d safety=%.2f tpw=%.2f]",
        config.OPENAI_MODEL, window, source, words, window,
        config.OUTPUT_RESERVE_TOKENS, config.PROMPT_OVERHEAD_TOKENS,
        config.CONTEXT_SAFETY_FRACTION, config.TOKENS_PER_WORD,
    )
    return {
        "enabled": True,
        "model": config.OPENAI_MODEL,
        "context_window": window,
        "source": source,
        "chunk_words": words,
        "num_ctx": window,
    }
