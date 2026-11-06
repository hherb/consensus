"""Shared HTTP GET for URL ingestion: retry, streaming size cap, URL guard.

Document and image ingestion both fetch user- or AI-supplied URLs. Each
used to carry its own loop and the image one had no retry at all (issue
#88), so the policy lives here once:

* transient faults (timeouts, transport errors, DNS failures, 408/425/429
  and 5xx other than 501/505) are attempted up to ``max_retries`` times in
  total, with exponential backoff (golden rule 5); any other non-2xx, a
  guard refusal, a redirect loop or a fault no retry can cure (invalid
  URL, unsupported scheme, undecodable body) is permanent;
* an optional ``deadline`` bounds the whole fetch, retries included, so a
  tool call's fetch fails with its own message before the tool timeout;
* the body is measured *while* it streams, so an oversized response is
  abandoned rather than buffered into memory first;
* an optional ``guard`` runs on every request, redirect hops included;
  :func:`require_public_host` is the SSRF guard every caller fetching a
  user- or AI-supplied URL passes, so a public URL cannot redirect the
  server to an internal address;
* URLs in messages and logs pass through :func:`redact_url`, because a
  signed URL's query string is a credential (issue #89).

Failures raise a :class:`FetchError` subclass whose ``str()`` is safe to
show the user.
"""

import asyncio
import ipaddress
import logging
import socket
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

#: 4xx statuses that mean "try again later" rather than "never".
RETRYABLE_CLIENT_STATUSES = frozenset({408, 425, 429})

#: 5xx statuses that no retry can cure (not implemented, bad HTTP version).
PERMANENT_SERVER_STATUSES = frozenset({501, 505})

# NAT64 (RFC 6052) embeds an IPv4 address in the low 32 bits; Python
# reports the IPv6 form as global even when the IPv4 one is loopback.
_NAT64_PREFIX = ipaddress.ip_network("64:ff9b::/96")

# IPv4-compatible IPv6 (::a.b.c.d, deprecated): first 96 bits all zero.
_IPV4_COMPAT_PREFIX_BYTES = bytes(12)


class FetchError(Exception):
    """A URL could not be fetched. ``str()`` is user-facing and redacted."""


class FetchRejected(FetchError):
    """Permanent refusal: a non-retryable status, a guard veto, a redirect
    loop, or a fault no retry can cure (invalid URL, unsupported scheme,
    undecodable body)."""


class FetchTooLarge(FetchError):
    """The body exceeds the caller's size cap."""


class FetchUnavailable(FetchError):
    """Transient faults outlasted every retry or the deadline.

    A guard raises it for a host that does not resolve, which is retried
    like any other transient fault.
    """


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


def is_retryable_status(status: int) -> bool:
    """Whether a non-2xx HTTP *status* is worth retrying.

    Args:
        status: The response status code.

    Returns:
        ``True`` for 408/425/429 and for 5xx other than 501/505.
    """
    if status in RETRYABLE_CLIENT_STATUSES:
        return True
    return 500 <= status < 600 and status not in PERMANENT_SERVER_STATUSES


def is_public_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Whether *ip* is a public unicast address a fetch may reach.

    ``is_global`` excludes private, loopback, link-local, reserved and
    CGNAT space; multicast is "global" but never a content host. An IPv4
    address embedded in NAT64 or IPv4-compatible IPv6 is judged as IPv4.

    Args:
        ip: The address to classify.

    Returns:
        ``True`` only for a public unicast address.
    """
    if ip.version == 6 and (ip in _NAT64_PREFIX
                            or ip.packed[:12] == _IPV4_COMPAT_PREFIX_BYTES):
        ip = ipaddress.IPv4Address(ip.packed[12:])
    return ip.is_global and not ip.is_multicast


def require_public_host(url: str) -> None:
    """SSRF guard: refuse a URL whose host is not a public address.

    Pass it as ``fetch_capped(guard=...)``; it runs on every request of a
    fetch, redirect hops included. A host with several addresses is
    refused if any one of them is internal.

    shortcut: the host is resolved here and again by httpx, so DNS
    rebinding between the two lookups is not prevented; pin the resolved
    address in a custom transport if multi-user deployments need that.

    Args:
        url: The request URL about to be sent.

    Raises:
        FetchRejected: The host resolves to a non-public address, or the
            URL has no host.
        FetchUnavailable: The host does not resolve (retried, since DNS
            failures are often transient).
    """
    try:
        host = urlsplit(url).hostname or ""
    except ValueError as e:
        raise FetchRejected(f"{redact_url(url)} is not a valid URL") from e
    if not host:
        raise FetchRejected(f"{redact_url(url)} has no host")
    try:
        infos = socket.getaddrinfo(host, None, socket.AF_UNSPEC,
                                   socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise FetchUnavailable(f"could not resolve {host}") from e
    for _family, _type, _proto, _canon, sockaddr in infos:
        try:
            ip = ipaddress.ip_address(sockaddr[0])
        except ValueError:
            ip = None  # unparseable (e.g. scoped) address: fail closed
        if ip is None or not is_public_ip(ip):
            raise FetchRejected(
                f"Refusing to fetch {host}: it resolves to a "
                "private/internal address")


# Faults no retry can cure. UnsupportedProtocol subclasses TransportError,
# so this is checked before the retry branch.
_PERMANENT_FAULTS = (httpx.InvalidURL, httpx.UnsupportedProtocol,
                     httpx.DecodingError)


def _declared_length(response: httpx.Response) -> int | None:
    """Return the ``content-length`` header as an int, or ``None``.

    A missing or non-numeric header is not trusted; the streamed cap in
    :func:`_read_capped` still bounds the body.
    """
    try:
        return int(response.headers.get("content-length", ""))
    except ValueError:
        return None


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
    deadline: float | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[bytes, str]:
    """GET *url* with retry, a streaming size cap and an optional guard.

    Args:
        url: The resource to fetch.
        max_bytes: Largest body accepted, by declared length or bytes read.
        timeout: Per-request timeout in seconds.
        max_retries: Attempts in total, the first included.
        base_delay: Seconds before the first retry; doubled each time.
        guard: Called with each request URL, redirect hops included; raises
            :class:`FetchRejected` to veto the request (or
            :class:`FetchUnavailable` for a transient refusal, which is
            retried). Usually :func:`require_public_host`.
        deadline: Seconds the whole fetch may take, retries and backoff
            included; ``None`` for no overall limit.
        headers: Extra request headers (e.g. a User-Agent).

    Returns:
        ``(body, content_type)``; ``content_type`` is the raw header value,
        or ``""`` when the server sent none.

    Raises:
        FetchRejected: On a non-retryable status, a guard veto, too many
            redirects, or a fault no retry can cure.
        FetchTooLarge: When the body exceeds *max_bytes*.
        FetchUnavailable: When every attempt failed transiently, or the
            deadline passed.
    """
    shown = redact_url(url)
    try:
        async with asyncio.timeout(deadline):
            return await _fetch_with_retries(
                url, shown, max_bytes=max_bytes, timeout=timeout,
                max_retries=max_retries, base_delay=base_delay, guard=guard,
                headers=headers)
    except TimeoutError:
        if deadline is None:
            raise
        raise FetchUnavailable(
            f"Could not fetch {shown} within {deadline:g}s") from None


async def _fetch_with_retries(
    url: str, shown: str, *, max_bytes: int, timeout: float,
    max_retries: int, base_delay: float,
    guard: Callable[[str], None] | None, headers: dict[str, str] | None,
) -> tuple[bytes, str]:
    """The retry loop of :func:`fetch_capped`, without the deadline."""
    hooks = {"request": [_guard_hook(guard)]} if guard else {}
    last_fault = ""
    async with httpx.AsyncClient(
        timeout=timeout, follow_redirects=True, event_hooks=hooks,
        headers=headers,
    ) as client:
        for attempt in range(max_retries):
            try:
                async with client.stream("GET", url) as response:
                    status = response.status_code
                    if not response.is_success:
                        if not is_retryable_status(status):
                            raise FetchRejected(
                                f"{shown} returned HTTP {status}")
                        response.raise_for_status()
                    declared = _declared_length(response)
                    if declared is not None and declared > max_bytes:
                        raise FetchTooLarge(
                            f"{shown} is too large ({declared} bytes; "
                            f"the limit is {max_bytes})")
                    content = await _read_capped(response, shown, max_bytes)
                    return content, response.headers.get("content-type", "")
            except httpx.TooManyRedirects as e:
                raise FetchRejected(
                    f"{shown} led to too many redirects") from e
            except _PERMANENT_FAULTS as e:
                # Retrying cannot help, and InvalidURL's text embeds the
                # raw URL, so only the type name is reported.
                raise FetchRejected(
                    f"{shown} could not be fetched ({type(e).__name__})"
                ) from e
            except (httpx.TimeoutException, httpx.HTTPStatusError,
                    httpx.TransportError, FetchUnavailable) as e:
                last_fault = _describe_fault(e)
                if attempt == max_retries - 1:
                    break
                delay = base_delay * (2 ** attempt)
                logger.warning(
                    "Fetch of %s failed (attempt %d/%d: %s), retrying in "
                    "%.1fs", shown, attempt + 1, max_retries, last_fault,
                    delay)
                await asyncio.sleep(delay)

    # Not chained: HTTPStatusError's text embeds the unredacted URL, and
    # last_fault already carries the redacted summary.
    raise FetchUnavailable(
        f"Could not fetch {shown} after {max_retries} attempts: {last_fault}")
