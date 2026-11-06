"""Fetching images from URLs, with the SSRF guard."""

from urllib.parse import urlparse

from ..http_fetch import (
    FetchRejected, fetch_capped, redact_url, require_public_host,
)
from .constants import MAX_IMAGE_BYTES, SUPPORTED_IMAGE_TYPES, URL_FETCH_TIMEOUT


async def fetch_image_from_url(
    url: str, deadline: float | None = None,
) -> tuple[bytes, str, str]:
    """Fetch an image from a URL.

    Transient faults are retried with backoff and the body is size-capped
    while it streams (``consensus.http_fetch``); every hop is SSRF-checked.
    A response without a ``Content-Type`` is assumed to be PNG; ingestion
    then refuses it if Pillow cannot decode it.

    Args:
        url: The image location.
        deadline: Seconds the whole fetch may take, retries included
            (``None`` for no overall limit).

    Returns:
        ``(content_bytes, filename, mime_type)``.

    Raises:
        FetchError: The URL was malformed, refused, unreachable, too large
            or not a supported image. ``str()`` is safe to show the user.
    """
    try:
        parsed = urlparse(url)
    except ValueError as e:  # e.g. an unclosed IPv6 bracket
        raise FetchRejected(f"{redact_url(url)} is not a valid URL") from e
    if parsed.scheme not in ("http", "https"):
        raise FetchRejected(f"Unsupported URL scheme: {parsed.scheme}")
    content, content_type = await fetch_capped(
        url, max_bytes=MAX_IMAGE_BYTES, timeout=URL_FETCH_TIMEOUT,
        guard=require_public_host, deadline=deadline,
    )
    mime_type = (content_type or "image/png").split(";")[0].strip()
    if mime_type not in SUPPORTED_IMAGE_TYPES:
        raise FetchRejected(
            f"URL does not point to a supported image type: {mime_type}")
    filename = parsed.path.split("/")[-1] or "image"
    return content, filename, mime_type
