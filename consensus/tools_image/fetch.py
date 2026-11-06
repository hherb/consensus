"""Fetching images from URLs, with the SSRF guard."""

from urllib.parse import urlparse

from ..http_fetch import FetchRejected, fetch_capped
from .constants import MAX_IMAGE_BYTES, SUPPORTED_IMAGE_TYPES, URL_FETCH_TIMEOUT


def _is_private_ip(hostname: str) -> bool:
    """Check if a hostname resolves to a private/internal IP address."""
    import ipaddress
    import socket
    try:
        addr = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
        for _family, _, _, _, sockaddr in addr:
            ip = ipaddress.ip_address(sockaddr[0])
            # is_global excludes private, loopback, link-local, reserved and
            # CGNAT space; multicast is "global" but never an image host.
            if not ip.is_global or ip.is_multicast:
                return True
    except (socket.gaierror, ValueError):
        return True  # treat unresolvable as blocked
    return False


def _require_public_host(url: str) -> None:
    """SSRF guard: refuse a URL whose host is not a public address.

    Runs on every request of a fetch, redirect hops included.

    shortcut: the host is resolved here and again by httpx, so DNS
    rebinding between the two lookups is not prevented; pin the resolved
    address in a custom transport if multi-user deployments need that.
    """
    if _is_private_ip(urlparse(url).hostname or ""):
        raise FetchRejected(
            "Cannot fetch images from private/internal addresses")


async def fetch_image_from_url(url: str) -> tuple[bytes, str, str]:
    """Fetch an image from a URL.

    Transient faults are retried with backoff and the body is size-capped
    while it streams (``consensus.http_fetch``); every hop is SSRF-checked.

    Returns:
        ``(content_bytes, filename, mime_type)``.

    Raises:
        FetchError: The URL was refused, unreachable, too large or not a
            supported image. ``str()`` is safe to show the user.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise FetchRejected(f"Unsupported URL scheme: {parsed.scheme}")
    content, content_type = await fetch_capped(
        url, max_bytes=MAX_IMAGE_BYTES, timeout=URL_FETCH_TIMEOUT,
        guard=_require_public_host,
    )
    mime_type = (content_type or "image/png").split(";")[0].strip()
    if mime_type not in SUPPORTED_IMAGE_TYPES:
        raise FetchRejected(
            f"URL does not point to a supported image type: {mime_type}")
    filename = parsed.path.split("/")[-1] or "image"
    return content, filename, mime_type
