"""URL validation (SSRF-safe) and safe fetching."""

import ipaddress
import socket
from urllib.parse import urlparse

import httpx

from . import events
from .config import MAX_HTML_BYTES, REQUEST_TIMEOUT, USER_AGENT


def _ip_is_blocked(ip_str) -> bool:
    """Return True if the IP is not a globally routable public address."""
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        # Unparseable address -> treat as blocked to be safe.
        return True
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_unspecified
        or ip.is_multicast
        or not ip.is_global
    )


def validate_url(url):
    """Validate scheme and resolve host, blocking private/local addresses.

    Returns (ok: bool, reason: str | None).
    """
    try:
        parsed = urlparse(url)
    except Exception:
        return False, "URL could not be parsed"

    if parsed.scheme not in ("http", "https"):
        return False, "Only http and https URLs are allowed"

    host = parsed.hostname
    if not host:
        return False, "URL has no host"

    # Block obvious local hostnames before DNS resolution.
    if host.lower() in ("localhost",):
        return False, "Private or local IP addresses are not allowed"

    # Resolve the host to all addresses and check each one.
    try:
        infos = socket.getaddrinfo(host, parsed.port or None)
    except socket.gaierror:
        return False, "Host could not be resolved"

    if not infos:
        return False, "Host could not be resolved"

    for info in infos:
        ip_str = info[4][0]
        if _ip_is_blocked(ip_str):
            return False, "Private or local IP addresses are not allowed"

    return True, None


async def fetch_url(url, emit):
    """Fetch a URL safely with SSRF re-validation, size cap, and one retry.

    Returns {"url", "html", "status", "content_type"} or None on failure.
    """
    await emit(events.log("Fetch started", url=url))

    last_error = None
    # Two attempts total: initial + one retry.
    for attempt in range(2):
        if attempt == 1:
            await emit(
                events.log(
                    "Retry attempt", level="warn", url=url, error=str(last_error)
                )
            )
        try:
            async with httpx.AsyncClient(
                timeout=REQUEST_TIMEOUT,
                follow_redirects=True,
                headers={"User-Agent": USER_AGENT},
            ) as client:
                response = await client.get(url)

            # Re-validate the final URL host after redirects.
            final_ok, final_reason = validate_url(str(response.url))
            if not final_ok:
                await emit(
                    events.log(
                        "Blocked after redirect",
                        level="error",
                        url=str(response.url),
                        reason=final_reason,
                    )
                )
                return None

            await emit(
                events.log("HTTP status", url=url, status=response.status_code)
            )

            # A non-2xx response is not usable content (e.g. a 403 block page);
            # fail fast rather than feed an error body downstream. No retry: the
            # status is unlikely to change on an immediate second attempt.
            if response.status_code >= 400:
                await emit(
                    events.log(
                        "HTTP error status",
                        level="error",
                        url=url,
                        status=response.status_code,
                    )
                )
                return None

            content_type = response.headers.get("content-type", "")
            await emit(events.log("Content type", url=url, content_type=content_type))

            content = response.content
            if len(content) > MAX_HTML_BYTES:
                await emit(
                    events.log(
                        "Response too large, truncating",
                        level="warn",
                        url=url,
                        bytes=len(content),
                        cap=MAX_HTML_BYTES,
                    )
                )
                content = content[:MAX_HTML_BYTES]

            html = content.decode(response.encoding or "utf-8", errors="replace")

            await emit(
                events.log("Raw HTML size", url=url, bytes=len(content))
            )

            if attempt == 1:
                await emit(events.log("Retry succeeded", level="success", url=url))

            await emit(events.log("Fetch completed", level="success", url=url))

            return {
                "url": url,
                "html": html,
                "status": response.status_code,
                "content_type": content_type,
            }
        except Exception as exc:  # noqa: BLE001 - we want to retry on anything
            last_error = exc
            if attempt == 1:
                await emit(
                    events.log(
                        "Retry failed", level="error", url=url, error=str(exc)
                    )
                )

    await emit(
        events.log(
            "Fetch failed", level="error", url=url, error=str(last_error)
        )
    )
    return None
