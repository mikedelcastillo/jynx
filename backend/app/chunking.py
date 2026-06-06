"""In-memory paragraph-aware chunking and diversity-aware selection."""

import re
from typing import List

from . import config

_MIN_CHUNK_WORDS = 150


def _word_count(text: str) -> int:
    return len(text.split())


def _split_paragraphs(text: str) -> List[str]:
    """Split on blank lines into non-empty paragraphs."""
    parts = re.split(r"\n\s*\n", text)
    return [p.strip() for p in parts if p.strip()]


def _split_long_paragraph(para: str, max_words: int) -> List[str]:
    """Hard-split a single oversized paragraph into ~max_words word windows.

    Extraction collapses whitespace, so a whole article often arrives as one
    paragraph with no blank-line breaks. Without this, such a source would
    become a single giant chunk (one huge LLM call); splitting it lets the
    per-chunk fan-out actually parallelize and keeps each call's context small.
    """
    words = para.split()
    if len(words) <= max_words:
        return [para]
    return [
        " ".join(words[i : i + max_words])
        for i in range(0, len(words), max_words)
    ]


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

        # Hard-split any paragraph longer than the target so a paragraph-less
        # source (the common case after whitespace-collapsing extraction) still
        # yields multiple chunks instead of one oversized one.
        paragraphs = [
            piece
            for para in paragraphs
            for piece in _split_long_paragraph(para, config.TARGET_CHUNK_WORDS)
        ]

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
            if current_words >= config.TARGET_CHUNK_WORDS:
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


def select_map_chunks(chunks: List[dict], max_chunks: int) -> List[dict]:
    """Pick at most `max_chunks` chunks for the parallel map, source-balanced.

    Each returned chunk becomes one concurrent LLM call, so this bounds the
    number of calls (and token cost). Pasted-text chunks come first, then a
    round-robin across the remaining sources so every source contributes before
    any single long source is exhausted.
    """
    if len(chunks) <= max_chunks:
        return list(chunks)

    selected: List[dict] = []

    # Always include pasted-text chunks first (up to the cap).
    pasted = [c for c in chunks if c["source"] == "pasted-text"]
    others = [c for c in chunks if c["source"] != "pasted-text"]

    for chunk in pasted:
        if len(selected) >= max_chunks:
            return selected
        selected.append(chunk)

    # Group remaining chunks by source, preserving order.
    by_source: dict = {}
    order: List[str] = []
    for chunk in others:
        if chunk["source"] not in by_source:
            by_source[chunk["source"]] = []
            order.append(chunk["source"])
        by_source[chunk["source"]].append(chunk)

    # Round-robin pull one chunk per source until the cap is reached or all
    # sources are exhausted.
    while len(selected) < max_chunks and any(by_source[s] for s in order):
        for source in order:
            bucket = by_source[source]
            if not bucket:
                continue
            selected.append(bucket.pop(0))
            if len(selected) >= max_chunks:
                break

    return selected
