"""HTML -> clean text extraction (trafilatura primary, BeautifulSoup fallback)."""

import re

import trafilatura
from bs4 import BeautifulSoup

from .config import MAX_EXTRACTED_CHARS

_DROP_TAGS = ("script", "style", "nav", "header", "footer", "aside")


def _clean_whitespace(text: str) -> str:
    """Collapse runs of whitespace/newlines into single spaces and strip."""
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def extract_text(html, url):
    """Extract readable text from HTML.

    Returns (text, used_fallback: bool). Text is whitespace-cleaned and capped.
    """
    used_fallback = False
    text = None

    try:
        extracted = trafilatura.extract(html, url=url)
    except Exception:
        extracted = None

    if extracted and extracted.strip():
        text = extracted
    else:
        used_fallback = True
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(list(_DROP_TAGS)):
            tag.decompose()
        text = soup.get_text(" ")

    text = _clean_whitespace(text or "")
    if len(text) > MAX_EXTRACTED_CHARS:
        text = text[:MAX_EXTRACTED_CHARS]

    return text, used_fallback
