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
* a ``Retry-After`` header can lengthen the wait before the next attempt
  (never below our own backoff); a wait longer than
  ``FETCH_MAX_RETRY_AFTER`` or the time left gives up at once;
* an optional ``guard`` runs on every request, redirect hops included;
  :func:`require_public_host` is the SSRF guard every caller fetching a
  user- or AI-supplied URL passes, so a public URL cannot redirect the
  server to an internal address. Unless an env or system proxy is set, the
  connection is pinned to the addresses the guard checked, so a DNS answer
  that changes after the check (rebinding) is never connected to (#102);
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
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from .http_pinning import pinned_transport

logger = logging.getLogger(__name__)

#: Attempts per fetch, the first included.
FETCH_MAX_RETRIES = 3

#: Seconds before the first retry; doubled for each later one.
FETCH_BASE_DELAY = 1.0

#: Longest ``Retry-After`` wait honoured; a longer one gives up at once.
FETCH_MAX_RETRY_AFTER = 30.0

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


def require_public_host(url: str) -> list[str]:
    """SSRF guard: refuse a URL whose host is not a public address.

    Pass it as ``fetch_capped(guard=...)``; it runs on every request of a
    fetch, redirect hops included. A host with several addresses is
    refused if any one of them is internal. :func:`fetch_capped` connects
    only to the addresses returned, so the host is not resolved again.

    Args:
        url: The request URL about to be sent.

    Returns:
        The host's addresses, each once, in resolver order.

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
    addresses: list[str] = []
    for _family, _type, _proto, _canon, sockaddr in infos:
        try:
            ip = ipaddress.ip_address(sockaddr[0])
        except ValueError:
            ip = None  # unparseable (e.g. scoped) address: fail closed
        if ip is None or not is_public_ip(ip):
            raise FetchRejected(
                f"Refusing to fetch {host}: it resolves to a "
                "private/internal address")
        if str(ip) not in addresses:
            addresses.append(str(ip))
    return addresses


def retry_after_seconds(value: str | None, now: datetime) -> float | None:
    """Parse a ``Retry-After`` header into seconds from *now*.

    Args:
        value: The header value: delay-seconds or an HTTP-date.
        now: The current time (timezone-aware), for an HTTP-date.

    Returns:
        The wait in seconds (a date in the past is ``0``), or ``None`` for
        a missing or unreadable header.
    """
    value = (value or "").strip()
    if value.isascii() and value.isdigit():  # isdigit alone accepts "²"
        return float(value)
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)  # RFC 9110: HTTP-dates are GMT
    return max(0.0, (when - now).total_seconds())


#: Checks a request URL; returns the addresses it may connect to.
Guard = Callable[[str], list[str]]

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


def _guard_hook(guard: Guard, pins: dict[str, list[str]]
                ) -> Callable[[httpx.Request], Awaitable[None]]:
    """Wrap a blocking *guard* as an httpx request hook.

    httpx runs request hooks before every hop of a redirect chain. The
    guard may resolve DNS, so it runs in a worker thread rather than
    blocking the event loop. The addresses it returns are recorded in
    *pins* under the request's ``raw_host``, the name httpcore connects to.
    A guard that returns none is a programming fault, refused at once
    rather than retried as a connection failure.
    """
    async def hook(request: httpx.Request) -> None:
        addresses = await asyncio.to_thread(guard, str(request.url))
        host = request.url.raw_host.decode("ascii")
        if not addresses:
            raise FetchRejected(f"the URL guard returned no address for {host}")
        pins[host] = list(addresses)
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
    guard: Guard | None = None,
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
            retried) and returns the addresses the request may connect to —
            nothing else is connected to unless a proxy applies. Usually
            :func:`require_public_host`.
        deadline: Seconds the whole fetch may take, retries and backoff
            included; ``None`` for no overall limit.
        headers: Extra request headers (e.g. a User-Agent).

    Returns:
        ``(body, content_type)``; ``content_type`` is the raw header value,
        or ``""`` when the server sent none.

    Raises:
        ValueError: *max_retries* is below 1.
        FetchRejected: On a non-retryable status, a guard veto, too many
            redirects, or a fault no retry can cure.
        FetchTooLarge: When the body exceeds *max_bytes*.
        FetchUnavailable: When every attempt failed transiently, a
            ``Retry-After`` asked for a longer wait than is left, or the
            deadline passed.
    """
    if max_retries < 1:
        raise ValueError(f"max_retries must be at least 1, not {max_retries}")
    shown = redact_url(url)
    loop = asyncio.get_running_loop()
    give_up_at = None if deadline is None else loop.time() + deadline
    try:
        async with asyncio.timeout(deadline):
            return await _fetch_with_retries(
                url, shown, max_bytes=max_bytes, timeout=timeout,
                max_retries=max_retries, base_delay=base_delay, guard=guard,
                headers=headers, give_up_at=give_up_at)
    except TimeoutError:
        if deadline is None:
            raise
        raise FetchUnavailable(
            f"Could not fetch {shown} within {deadline:g}s") from None


async def _fetch_with_retries(
    url: str, shown: str, *, max_bytes: int, timeout: float,
    max_retries: int, base_delay: float,
    guard: Guard | None, headers: dict[str, str] | None,
    give_up_at: float | None,
) -> tuple[bytes, str]:
    """The retry loop of :func:`fetch_capped`, without the deadline.

    *give_up_at* is the deadline in ``loop.time()`` terms, so a
    ``Retry-After`` past it fails now instead of sleeping into the timeout.
    """
    client_kwargs: dict[str, Any] = {}
    if guard:
        pins: dict[str, list[str]] = {}
        client_kwargs["event_hooks"] = {"request": [_guard_hook(guard, pins)]}
        transport = pinned_transport(pins)
        if transport is not None:
            client_kwargs["transport"] = transport
    last_fault = ""
    attempts = 0
    async with httpx.AsyncClient(
        timeout=timeout, follow_redirects=True, headers=headers,
        **client_kwargs,
    ) as client:
        for attempt in range(max_retries):
            attempts = attempt + 1
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
                asked = _asked_wait(e)
                if asked is not None:
                    left = (FETCH_MAX_RETRY_AFTER if give_up_at is None else
                            min(FETCH_MAX_RETRY_AFTER,
                                give_up_at - asyncio.get_running_loop().time()))
                    last_fault += f" (retry after {asked:g}s)"
                    if asked > left:
                        break
                    delay = max(delay, asked)
                logger.warning(
                    "Fetch of %s failed (attempt %d/%d: %s), retrying in "
                    "%.1fs", shown, attempt + 1, max_retries, last_fault,
                    delay)
                await asyncio.sleep(delay)

    # Not chained: HTTPStatusError's text embeds the unredacted URL, and
    # last_fault already carries the redacted summary.
    plural = "" if attempts == 1 else "s"
    raise FetchUnavailable(
        f"Could not fetch {shown} after {attempts} attempt{plural}: "
        f"{last_fault}")


def _asked_wait(exc: Exception) -> float | None:
    """The ``Retry-After`` wait of a retryable status reply, if it sent one."""
    if not isinstance(exc, httpx.HTTPStatusError):
        return None
    return retry_after_seconds(exc.response.headers.get("retry-after"),
                               datetime.now(timezone.utc))
