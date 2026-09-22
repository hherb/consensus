"""Document parsing and section extraction.

Converts uploaded or fetched bytes (PDF, HTML, plain text or markdown) into
markdown, and extracts the markdown header structure with character offsets.
"""

import asyncio
import logging
import re
from dataclasses import dataclass, field

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
    notes: list[str] = field(default_factory=list)


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
    return ParsedDocument(markdown=text)


def _parse_pdf(content: bytes) -> ParsedDocument:
    """Extract text from PDF bytes, preferring pdfplumber.

    Raises:
        DocumentParseError: If neither pdfplumber nor PyPDF2 is installed,
            the PDF cannot be opened, or no page yields extractable text —
            the last case being a scanned or image-only PDF, which
            previously ingested as the literal string ``"(Empty PDF)"``
            (issue #78 defect 7).
    """
    try:
        import io

        import pdfplumber
        pages = []
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            for i, page in enumerate(pdf.pages):
                text = page.extract_text() or ""
                if text.strip():
                    pages.append(f"## Page {i + 1}\n\n{text}")
        if pages:
            return ParsedDocument(markdown="\n\n".join(pages))
        logger.info("pdfplumber extracted no text, trying PyPDF2")
    except ImportError:
        logger.info("pdfplumber not available, trying PyPDF2")
    except Exception as e:
        logger.warning("pdfplumber failed: %s, trying PyPDF2", e)

    try:
        import io

        from PyPDF2 import PdfReader
        reader = PdfReader(io.BytesIO(content))
        pages = []
        for i, page in enumerate(reader.pages):
            text = page.extract_text() or ""
            if text.strip():
                pages.append(f"## Page {i + 1}\n\n{text}")
        if pages:
            return ParsedDocument(markdown="\n\n".join(pages))
    except ImportError:
        raise DocumentParseError(
            "PDF parsing requires pdfplumber or PyPDF2",
            hint="install with: uv pip install pdfplumber",
        )
    except Exception as e:
        raise DocumentParseError(
            f"PDF could not be read: {e}",
            hint="the file may be corrupt or password-protected",
        ) from e

    raise DocumentParseError(
        "No extractable text in this PDF — it looks scanned or image-only",
        hint="OCR the file before adding it",
    )


def _parse_html(content: bytes) -> ParsedDocument:
    """Extract readable text from HTML, marking regex fallbacks degraded.

    Raises:
        DocumentParseError: If neither trafilatura nor the regex fallback
            can find any readable text — the page is effectively empty.
    """
    html_text = content.decode("utf-8", errors="replace")
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
        notes=[
            "Readability extraction failed; this text was produced by "
            "stripping HTML tags and may contain navigation, cookie "
            "banners or script content.",
        ],
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
            its declared ``content-length`` or by its actual read size.
    """
    last_exc: Exception | None = None
    async with httpx.AsyncClient(
        timeout=URL_FETCH_TIMEOUT, follow_redirects=True,
    ) as client:
        for attempt in range(URL_FETCH_MAX_RETRIES):
            try:
                response = await client.get(url)

                if 400 <= response.status_code < 500:
                    raise DocumentParseError(
                        f"{url} returned HTTP {response.status_code}",
                        hint="check the address, or whether it needs a login",
                    )
                response.raise_for_status()

                declared = response.headers.get("content-length")
                if declared and int(declared) > MAX_DOCUMENT_BYTES:
                    raise DocumentParseError(
                        f"{url} is too large ({int(declared)} bytes; the "
                        f"limit is {MAX_DOCUMENT_BYTES})",
                        hint="download it and add the relevant extract",
                    )

                content = response.content
                if len(content) > MAX_DOCUMENT_BYTES:
                    raise DocumentParseError(
                        f"{url} is too large ({len(content)} bytes; the "
                        f"limit is {MAX_DOCUMENT_BYTES})",
                        hint="download it and add the relevant extract",
                    )

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
