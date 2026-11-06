"""Shared HTTP GET for URL ingestion: retry, streaming size cap, URL guard.

Document and image ingestion both fetch user- or AI-supplied URLs. Each
used to carry its own loop and the image one had no retry at all (issue
#88), so the policy lives here once:

* transient faults (timeouts, transport errors, 5xx) are retried up to
  ``max_retries`` times with exponential backoff (golden rule 5); a 4xx,
  a guard refusal or a redirect loop is permanent;
* the body is measured *while* it streams, so an oversized response is
  abandoned rather than buffered into memory first;
* an optional ``guard`` runs on every request, redirect hops included, so
  a public URL cannot redirect the server to an internal address;
* URLs in messages and logs pass through :func:`redact_url`, because a
  signed URL's query string is a credential (issue #89).

Failures raise a :class:`FetchError` subclass whose ``str()`` is safe to
show the user.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit, urlunsplit

import httpx

logger = logging.getLogger(__name__)

#: Attempts per fetch, the first included.
FETCH_MAX_RETRIES = 3

#: Seconds before the first retry; doubled for each later one.
FETCH_BASE_DELAY = 1.0

#: What :func:`redact_url` shows for a string ``urlsplit`` rejects.
UNPARSEABLE_URL = "<unparseable URL>"


class FetchError(Exception):
    """A URL could not be fetched. ``str()`` is user-facing and redacted."""


class FetchRejected(FetchError):
    """Permanent refusal: a 4xx, a guard veto or a redirect loop."""


class FetchTooLarge(FetchError):
    """The body exceeds the caller's size cap."""


class FetchUnavailable(FetchError):
    """Transient faults outlasted every retry."""


def redact_url(url: str) -> str:
    """Drop the userinfo, query string and fragment from *url*.

    Those are where signed URLs and basic-auth URLs carry their secrets;
    scheme, host, port and path are kept so a log line still identifies
    the resource.

    Args:
        url: Any string.

    Returns:
        The redacted URL, or ``UNPARSEABLE_URL`` when *url* cannot be
        split — echoing it could leak the very query it carries.
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return UNPARSEABLE_URL
    netloc = parts.netloc.rpartition("@")[2]
    return urlunsplit((parts.scheme, netloc, parts.path, "", ""))


def _describe_fault(exc: Exception) -> str:
    """Summarise a transient fault without echoing the request URL.

    ``HTTPStatusError``'s own message embeds the full URL, query included.
    """
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code}"
    return str(exc) or type(exc).__name__


def _guard_hook(guard: Callable[[str], None]
                ) -> Callable[[httpx.Request], Awaitable[None]]:
    """Wrap a blocking *guard* as an httpx request hook.

    httpx runs request hooks before every hop of a redirect chain. The
    guard may resolve DNS, so it runs in a worker thread rather than
    blocking the event loop.
    """
    async def hook(request: httpx.Request) -> None:
        await asyncio.to_thread(guard, str(request.url))
    return hook


async def _read_capped(response: httpx.Response, shown_url: str,
                       max_bytes: int) -> bytes:
    """Accumulate a streaming body, aborting as soon as it passes *max_bytes*.

    Measuring after the fact would buffer a chunked multi-gigabyte
    response in full — the OOM the cap exists to prevent (issue #78).
    """
    chunks: list[bytes] = []
    total = 0
    async for chunk in response.aiter_bytes():
        total += len(chunk)
        if total > max_bytes:
            raise FetchTooLarge(
                f"{shown_url} is too large (over {max_bytes} bytes; the "
                "transfer was aborted)")
        chunks.append(chunk)
    return b"".join(chunks)


async def fetch_capped(
    url: str,
    *,
    max_bytes: int,
    timeout: float,
    max_retries: int = FETCH_MAX_RETRIES,
    base_delay: float = FETCH_BASE_DELAY,
    guard: Callable[[str], None] | None = None,
) -> tuple[bytes, str]:
    """GET *url* with retry, a streaming size cap and an optional guard.

    Args:
        url: The resource to fetch.
        max_bytes: Largest body accepted, by declared length or bytes read.
        timeout: Per-request timeout in seconds.
        max_retries: Attempts in total, the first included.
        base_delay: Seconds before the first retry; doubled each time.
        guard: Called with each request URL, redirect hops included; raises
            :class:`FetchRejected` to veto the request.

    Returns:
        ``(body, content_type)``; ``content_type`` is the raw header value,
        or ``""`` when the server sent none.

    Raises:
        FetchRejected: On a 4xx, a guard veto or too many redirects.
        FetchTooLarge: When the body exceeds *max_bytes*.
        FetchUnavailable: When every attempt failed transiently.
    """
    shown = redact_url(url)
    hooks = {"request": [_guard_hook(guard)]} if guard else {}
    last_fault = ""
    async with httpx.AsyncClient(
        timeout=timeout, follow_redirects=True, event_hooks=hooks,
    ) as client:
        for attempt in range(max_retries):
            try:
                async with client.stream("GET", url) as response:
                    if 400 <= response.status_code < 500:
                        raise FetchRejected(
                            f"{shown} returned HTTP {response.status_code}")
                    response.raise_for_status()
                    declared = response.headers.get("content-length")
                    if declared and int(declared) > max_bytes:
                        raise FetchTooLarge(
                            f"{shown} is too large ({int(declared)} bytes; "
                            f"the limit is {max_bytes})")
                    content = await _read_capped(response, shown, max_bytes)
                    return content, response.headers.get("content-type", "")
            except httpx.TooManyRedirects as e:
                raise FetchRejected(
                    f"{shown} led to too many redirects") from e
            except (httpx.TimeoutException, httpx.HTTPStatusError,
                    httpx.TransportError) as e:
                last_fault = _describe_fault(e)
                if attempt == max_retries - 1:
                    break
                delay = base_delay * (2 ** attempt)
                logger.warning(
                    "Fetch of %s failed (attempt %d/%d: %s), retrying in "
                    "%.1fs", shown, attempt + 1, max_retries, last_fault,
                    delay)
                await asyncio.sleep(delay)

    raise FetchUnavailable(
        f"Could not fetch {shown} after {max_retries} attempts: {last_fault}")
