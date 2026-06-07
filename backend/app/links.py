"""Extract and normalize <a href> anchors from raw HTML for crawling.

The crawl loop (app/pipeline.py) needs the links on a page, not its clean text,
so this is a separate concern from extraction.py (HTML -> readable text).
BeautifulSoup4 is already a project dependency (see requirements.txt).
"""

from urllib.parse import urldefrag, urljoin, urlparse

from bs4 import BeautifulSoup

# Anchor schemes we never want to follow even before SSRF validation.
_SKIP_PREFIXES = ("javascript:", "mailto:", "tel:", "#")

# Cap on visible anchor text kept per link, so a single huge anchor can't bloat
# the relevance selector prompt.
_MAX_ANCHOR_TEXT = 200


def normalize_url(url: str) -> str:
    """Normalize a URL for dedup/visited-set comparison.

    Strips the #fragment (so `page#a` and `page#b` collapse to one page) and
    trailing whitespace. Kept deliberately conservative — when in doubt we'd
    rather over-fetch a near-duplicate than wrongly skip a real page; the SSRF
    validate_url() re-checks every URL before it is fetched anyway.
    """
    cleaned, _ = urldefrag(url)
    return cleaned.rstrip()


def extract_links(html: str, base_url: str) -> list[dict]:
    """Return a deduped list of {"url", "text"} anchors from raw HTML.

    - Absolutizes relative hrefs against base_url (urljoin).
    - Strips URL fragments so #anchor variants collapse to one URL.
    - Keeps only http/https schemes (drops javascript:/mailto:/tel:/#...).
    - Dedupes by normalized URL, preserving first-seen order and the first
      non-empty visible anchor text.

    SSRF validation is intentionally NOT done here — the caller runs
    validate_url() on each candidate as the security gate.
    """
    soup = BeautifulSoup(html, "html.parser")
    seen: set[str] = set()
    out: list[dict] = []

    for anchor in soup.find_all("a", href=True):
        raw = (anchor.get("href") or "").strip()
        if not raw or raw.lower().startswith(_SKIP_PREFIXES):
            continue

        absolute = normalize_url(urljoin(base_url, raw))
        if urlparse(absolute).scheme.lower() not in ("http", "https"):
            continue
        if absolute in seen:
            continue

        seen.add(absolute)
        text = " ".join(anchor.get_text(" ").split())[:_MAX_ANCHOR_TEXT]
        out.append({"url": absolute, "text": text})

    return out
