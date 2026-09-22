"""Document parsing and section extraction.

Converts uploaded or fetched bytes (PDF, HTML, plain text or markdown) into
markdown, and extracts the markdown header structure with character offsets.
"""

import asyncio
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass

import httpx

from .constants import (
    FIDELITY_DEGRADED, FIDELITY_FULL, MAX_DOCUMENT_BYTES,
    MAX_REPLACEMENT_CHAR_RATIO, URL_FETCH_BASE_DELAY, URL_FETCH_MAX_RETRIES,
    URL_FETCH_TIMEOUT,
)
from .errors import DocumentParseError

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Document parsing
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ParsedDocument:
    """The outcome of turning document bytes into markdown.

    Attributes:
        markdown: The extracted text.
        fidelity: ``FIDELITY_FULL`` when a real extractor produced the
            text, ``FIDELITY_DEGRADED`` when a fallback did.  A degraded
            extraction is still usable, but the AI is told so rather than
            being handed cookie banners as the document (issue #78).
        notes: Human-readable reasons for a degraded result.
    """

    markdown: str
    fidelity: str = FIDELITY_FULL
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Reject the states the parser should have raised on instead.

        ``frozen=True`` blocks rebinding, not mutation, so ``notes`` is a
        tuple: a list would stay appendable and would make ``__hash__``
        raise despite the dataclass being frozen.

        Raises:
            ValueError: If ``fidelity`` is not one of the two known values,
                or a degraded extraction records no reason. A typo'd
                fidelity compares unequal to *both* constants, so a
                downstream ``== FIDELITY_DEGRADED`` check would silently
                read it as full fidelity.
        """
        if self.fidelity not in (FIDELITY_FULL, FIDELITY_DEGRADED):
            raise ValueError(
                f"fidelity must be {FIDELITY_FULL!r} or "
                f"{FIDELITY_DEGRADED!r}, got {self.fidelity!r}"
            )
        if self.fidelity == FIDELITY_DEGRADED and not self.notes:
            raise ValueError("a degraded extraction must record why")


def parse_document(
    content: bytes, filename: str, mime_type: str,
) -> ParsedDocument:
    """Convert document bytes to markdown text.

    Supported formats:
    - PDF: pdfplumber (preferred) or PyPDF2 fallback
    - HTML: trafilatura, with a regex tag-stripper fallback
    - Plain text / Markdown: decoded as UTF-8

    Raises:
        DocumentParseError: If no usable text could be extracted, or the
            bytes are binary in a format this package cannot read.  It
            raises rather than synthesising placeholder content, which
            previously ingested as a real document (issue #78 defect 7).
    """
    if mime_type == "application/pdf" or filename.lower().endswith(".pdf"):
        return _parse_pdf(content)
    if mime_type in ("text/html", "application/xhtml+xml") or \
            filename.lower().endswith((".html", ".htm")):
        return _parse_html(content)
    return _parse_text(content, filename, mime_type)


def _parse_text(
    content: bytes, filename: str, mime_type: str,
) -> ParsedDocument:
    """Decode plain text or markdown, rejecting binary payloads.

    Args:
        content: The raw document bytes.
        filename: Used only for the error message.
        mime_type: Used only for the error message.

    Returns:
        A full-fidelity ``ParsedDocument``.

    Raises:
        DocumentParseError: If the bytes contain a NUL byte (a strong binary
            signal) or decode with more than ``MAX_REPLACEMENT_CHAR_RATIO``
            of the result being U+FFFD replacement characters — the mojibake
            an unrecognised binary format produces under
            ``errors="replace"`` (issue #78 defect 7).
    """
    return ParsedDocument(markdown=_decode_text(content, filename, mime_type))


def _decode_text(content: bytes, filename: str, mime_type: str) -> str:
    """Decode bytes as UTF-8, rejecting what is really binary.

    Shared by the plain-text and HTML parsers: an unrecognised binary blob
    served as ``text/html`` used to bypass this check entirely and ingest as
    tag-stripped mojibake (issue #78 whole-branch review).

    Args:
        content: The raw bytes.
        filename: Used only for the error message.
        mime_type: Used only for the error message.

    Returns:
        The decoded text.

    Raises:
        DocumentParseError: If the bytes contain a NUL byte (a strong binary
            signal) or decode with more than ``MAX_REPLACEMENT_CHAR_RATIO``
            of the result being U+FFFD replacement characters.
    """
    if b"\x00" in content:
        raise DocumentParseError(
            f"{filename} is binary, not text ({mime_type})",
            hint="only PDF, HTML, plain text and markdown can be ingested",
        )
    text = content.decode("utf-8", errors="replace")
    if text:
        ratio = text.count("�") / len(text)
        if ratio > MAX_REPLACEMENT_CHAR_RATIO:
            raise DocumentParseError(
                f"{filename} does not decode as UTF-8 text "
                f"({ratio:.0%} unreadable characters) — it looks binary",
                hint="only PDF, HTML, plain text and markdown can be ingested",
            )
    return text


def _pages_via_pdfplumber(content: bytes) -> list[str]:
    """Extract one markdown block per non-blank page using pdfplumber.

    Args:
        content: The raw PDF bytes.

    Returns:
        A ``"## Page N"`` block per page that yielded text; empty when the
        PDF opened cleanly but carries no extractable text.

    Raises:
        ImportError: If pdfplumber is not installed.
        Exception: Whatever pdfplumber raises for an unreadable PDF.
    """
    import io

    import pdfplumber
    pages: list[str] = []
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        for i, page in enumerate(pdf.pages):
            text = page.extract_text() or ""
            if text.strip():
                pages.append(f"## Page {i + 1}\n\n{text}")
    return pages


def _pages_via_pypdf2(content: bytes) -> list[str]:
    """Extract one markdown block per non-blank page using PyPDF2.

    Args:
        content: The raw PDF bytes.

    Returns:
        A ``"## Page N"`` block per page that yielded text; empty when the
        PDF opened cleanly but carries no extractable text.

    Raises:
        ImportError: If PyPDF2 is not installed.
        Exception: Whatever PyPDF2 raises for an unreadable PDF.
    """
    import io

    from PyPDF2 import PdfReader
    pages: list[str] = []
    for i, page in enumerate(PdfReader(io.BytesIO(content)).pages):
        text = page.extract_text() or ""
        if text.strip():
            pages.append(f"## Page {i + 1}\n\n{text}")
    return pages


# Tried in order; the first backend that yields any text wins.
_PDF_BACKENDS: tuple[tuple[str, Callable[[bytes], list[str]]], ...] = (
    ("pdfplumber", _pages_via_pdfplumber),
    ("PyPDF2", _pages_via_pypdf2),
)


def _parse_pdf(content: bytes) -> ParsedDocument:
    """Extract text from PDF bytes, preferring pdfplumber.

    Three outcomes are kept apart, because they need three different
    remedies and used to collapse into one misleading message: pdfplumber
    is a declared dependency while PyPDF2 is not, so an image-only PDF
    fell through pdfplumber, hit PyPDF2's ``ImportError``, and told the
    user to install the library they already had — making the OCR hint
    unreachable in every default install (issue #78 whole-branch review).

    Args:
        content: The raw PDF bytes.

    Returns:
        A full-fidelity ``ParsedDocument`` of the page text.

    Raises:
        DocumentParseError: If a backend read the file but no page yields
            text (scanned or image-only, the OCR case); if no backend could
            be imported at all (the install case); or if every imported
            backend failed to read the file (the corrupt case).
    """
    imported_any = False
    read_any = False
    last_read_error: Exception | None = None

    for name, extract in _PDF_BACKENDS:
        try:
            pages = extract(content)
        except ImportError:
            logger.info("%s is not installed — trying the next PDF backend",
                        name)
            continue
        except Exception as e:
            # The module imported, so the library exists; it simply could
            # not read these bytes.
            imported_any = True
            last_read_error = e
            logger.warning("%s could not read the PDF: %s", name, e)
            continue

        imported_any = True
        read_any = True
        if pages:
            return ParsedDocument(markdown="\n\n".join(pages))
        logger.info("%s opened the PDF but extracted no text", name)

    if read_any:
        raise DocumentParseError(
            "No extractable text in this PDF — it looks scanned or "
            "image-only",
            hint="OCR the file before adding it",
        )
    if not imported_any:
        raise DocumentParseError(
            "PDF parsing requires pdfplumber or PyPDF2",
            hint="install with: uv pip install pdfplumber",
        )
    raise DocumentParseError(
        f"PDF could not be read: {last_read_error}",
        hint="the file may be corrupt or password-protected",
    ) from last_read_error


def _parse_html(content: bytes) -> ParsedDocument:
    """Extract readable text from HTML, marking regex fallbacks degraded.

    Raises:
        DocumentParseError: If the bytes are really binary, or if neither
            trafilatura nor the regex fallback can find any readable text —
            the page is effectively empty.
    """
    html_text = _decode_text(content, "the HTML document", "text/html")
    try:
        import trafilatura
        text = trafilatura.extract(
            html_text, include_comments=False, include_tables=True,
        )
        if text:
            return ParsedDocument(markdown=text)
        logger.warning(
            "trafilatura extracted nothing — using the regex fallback; "
            "the page may be paywalled, consent-walled or JS-rendered",
        )
    except ImportError:
        logger.warning("trafilatura not available — using the regex fallback")
    except Exception as e:
        logger.warning("trafilatura failed (%s) — using the regex fallback", e)

    stripped = re.sub(r"<[^>]+>", "", html_text).strip()
    if not stripped:
        raise DocumentParseError(
            "No readable text could be extracted from this HTML",
            hint="the page may require JavaScript or be behind a paywall",
        )
    return ParsedDocument(
        markdown=stripped,
        fidelity=FIDELITY_DEGRADED,
        notes=(
            "Readability extraction failed; this text was produced by "
            "stripping HTML tags and may contain navigation, cookie "
            "banners or script content.",
        ),
    )


def _filename_for(url: str, mime_type: str) -> str:
    """Derive a filename with a useful extension from a URL and MIME type."""
    from urllib.parse import urlparse
    path = urlparse(url).path
    filename = path.split("/")[-1] or "document"
    if not filename.endswith((".pdf", ".html", ".htm", ".txt", ".md")):
        if "pdf" in mime_type:
            filename += ".pdf"
        elif "html" in mime_type:
            filename += ".html"
    return filename


async def _read_capped(response, url: str) -> bytes:
    """Accumulate a streaming response body, aborting past the size cap.

    Enforcing the cap *while* the body streams in is the whole point: a
    chunked (header-less) multi-gigabyte response would otherwise be
    buffered into memory in full and only then measured, so the guard that
    exists to prevent an OOM caused one (issue #78 whole-branch review).

    Args:
        response: An open streaming ``httpx.Response``.
        url: Used only in the error message.

    Returns:
        The complete body bytes.

    Raises:
        DocumentParseError: As soon as the bytes read exceed
            ``MAX_DOCUMENT_BYTES``; the rest of the body is never read.
    """
    chunks: list[bytes] = []
    total = 0
    async for chunk in response.aiter_bytes():
        total += len(chunk)
        if total > MAX_DOCUMENT_BYTES:
            raise DocumentParseError(
                f"{url} is too large (over {MAX_DOCUMENT_BYTES} bytes; the "
                "transfer was aborted)",
                hint="download it and add the relevant extract",
            )
        chunks.append(chunk)
    return b"".join(chunks)


async def fetch_url_content(url: str) -> tuple[bytes, str, str]:
    """Fetch a document from a URL.

    Retries transient failures — timeouts, connection errors and 5xx — up
    to ``URL_FETCH_MAX_RETRIES`` times with exponential backoff (golden
    rule 5).  A 4xx is permanent and raises immediately.

    Args:
        url: The document location to fetch.

    Returns:
        ``(content_bytes, filename, mime_type)``.

    Raises:
        DocumentParseError: If the fetch fails after all retries, a 4xx is
            returned, or the body exceeds ``MAX_DOCUMENT_BYTES`` — either by
            its declared ``content-length`` or by the bytes actually
            streamed, whichever announces itself first.
    """
    last_exc: Exception | None = None
    async with httpx.AsyncClient(
        timeout=URL_FETCH_TIMEOUT, follow_redirects=True,
    ) as client:
        for attempt in range(URL_FETCH_MAX_RETRIES):
            try:
                # Streamed, not buffered: the body is measured as it
                # arrives so an oversized one is abandoned mid-transfer.
                async with client.stream("GET", url) as response:
                    if 400 <= response.status_code < 500:
                        raise DocumentParseError(
                            f"{url} returned HTTP {response.status_code}",
                            hint=("check the address, or whether it needs "
                                  "a login"),
                        )
                    response.raise_for_status()

                    declared = response.headers.get("content-length")
                    if declared and int(declared) > MAX_DOCUMENT_BYTES:
                        raise DocumentParseError(
                            f"{url} is too large ({int(declared)} bytes; the "
                            f"limit is {MAX_DOCUMENT_BYTES})",
                            hint="download it and add the relevant extract",
                        )

                    content = await _read_capped(response, url)
                    content_type = response.headers.get(
                        "content-type", "text/html")

                mime_type = content_type.split(";")[0].strip()
                return content, _filename_for(url, mime_type), mime_type

            except DocumentParseError:
                raise
            except (httpx.TimeoutException, httpx.HTTPStatusError,
                    httpx.TransportError) as e:
                last_exc = e
                if attempt == URL_FETCH_MAX_RETRIES - 1:
                    break
                delay = URL_FETCH_BASE_DELAY * (2 ** attempt)
                logger.warning(
                    "Fetch of %s failed (attempt %d/%d: %s), retrying in "
                    "%.1fs", url, attempt + 1, URL_FETCH_MAX_RETRIES, e,
                    delay,
                )
                await asyncio.sleep(delay)

    raise DocumentParseError(
        f"Could not fetch {url} after {URL_FETCH_MAX_RETRIES} attempts: "
        f"{last_exc}",
        hint="check the address and that the host is reachable",
    )


# ---------------------------------------------------------------------------
# Section extraction
# ---------------------------------------------------------------------------

_HEADER_RE = re.compile(r"^(#{1,4})\s+(.+)$", re.MULTILINE)


def extract_sections(markdown: str) -> list[dict]:
    """Extract markdown headers with character offsets.

    Returns [{header, level, from_char, to_char}, ...]
    where from_char is the start of the section and to_char is the start
    of the next section (or end of document).
    """
    sections = []
    for match in _HEADER_RE.finditer(markdown):
        level = len(match.group(1))
        header = match.group(2).strip()
        from_char = match.start()
        sections.append({
            "header": header,
            "level": level,
            "from_char": from_char,
            "to_char": len(markdown),  # will be updated below
        })

    # Fix to_char: each section ends where the next one begins
    for i in range(len(sections) - 1):
        sections[i]["to_char"] = sections[i + 1]["from_char"]

    return sections
