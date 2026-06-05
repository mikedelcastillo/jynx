"""In-memory paragraph-aware chunking and diversity-aware selection."""

import re
from typing import List

from .config import MAX_CONTEXT_CHARS, TARGET_CHUNK_WORDS

_MIN_CHUNK_WORDS = 150


def _word_count(text: str) -> int:
    return len(text.split())


def _split_paragraphs(text: str) -> List[str]:
    """Split on blank lines into non-empty paragraphs."""
    parts = re.split(r"\n\s*\n", text)
    return [p.strip() for p in parts if p.strip()]


def chunk_sources(sources: List[dict]) -> List[dict]:
    """Chunk each source's text into ~TARGET_CHUNK_WORDS chunks.

    Each source is {"label", "text"}. Returns chunks of
    {"source", "text", "words", "chars"}.
    """
    chunks: List[dict] = []

    for source in sources:
        label = source["label"]
        paragraphs = _split_paragraphs(source["text"])

        # If there were no blank-line breaks, treat the whole text as one paragraph.
        if not paragraphs and source["text"].strip():
            paragraphs = [source["text"].strip()]

        current_parts: List[str] = []
        current_words = 0

        def flush():
            nonlocal current_parts, current_words
            if not current_parts:
                return
            text = "\n\n".join(current_parts)
            chunks.append(
                {
                    "source": label,
                    "text": text,
                    "words": _word_count(text),
                    "chars": len(text),
                }
            )
            current_parts = []
            current_words = 0

        for para in paragraphs:
            current_parts.append(para)
            current_words += _word_count(para)
            if current_words >= TARGET_CHUNK_WORDS:
                flush()
        flush()

    # Merge tiny fragments into the previous chunk from the same source.
    merged: List[dict] = []
    for chunk in chunks:
        if (
            merged
            and chunk["words"] < _MIN_CHUNK_WORDS
            and merged[-1]["source"] == chunk["source"]
        ):
            prev = merged[-1]
            new_text = prev["text"] + "\n\n" + chunk["text"]
            merged[-1] = {
                "source": prev["source"],
                "text": new_text,
                "words": _word_count(new_text),
                "chars": len(new_text),
            }
        else:
            merged.append(chunk)

    return merged


def select_chunks(chunks: List[dict]) -> List[dict]:
    """Round-robin across sources for diversity, always including pasted-text.

    Stops once total chars >= MAX_CONTEXT_CHARS.
    """
    selected: List[dict] = []
    total_chars = 0

    # Always include pasted-text chunks first.
    pasted = [c for c in chunks if c["source"] == "pasted-text"]
    others = [c for c in chunks if c["source"] != "pasted-text"]

    for chunk in pasted:
        selected.append(chunk)
        total_chars += chunk["chars"]

    if total_chars >= MAX_CONTEXT_CHARS:
        return selected

    # Group remaining chunks by source, preserving order.
    by_source: dict = {}
    order: List[str] = []
    for chunk in others:
        if chunk["source"] not in by_source:
            by_source[chunk["source"]] = []
            order.append(chunk["source"])
        by_source[chunk["source"]].append(chunk)

    # Round-robin pull one chunk per source until exhausted or cap reached.
    while total_chars < MAX_CONTEXT_CHARS and any(
        by_source[s] for s in order
    ):
        for source in order:
            bucket = by_source[source]
            if not bucket:
                continue
            chunk = bucket.pop(0)
            selected.append(chunk)
            total_chars += chunk["chars"]
            if total_chars >= MAX_CONTEXT_CHARS:
                break

    return selected
