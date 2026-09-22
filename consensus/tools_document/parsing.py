"""Document parsing and section extraction.

Converts uploaded or fetched bytes (PDF, HTML, plain text or markdown) into
markdown, and extracts the markdown header structure with character offsets.
"""

import logging
import re

import httpx

from .constants import URL_FETCH_TIMEOUT

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Document parsing
# ---------------------------------------------------------------------------

def parse_document(content: bytes, filename: str, mime_type: str) -> str:
    """Convert document bytes to markdown text.

    Supported formats:
    - PDF: pdfplumber (preferred) or PyPDF2 fallback
    - HTML: trafilatura
    - Plain text / Markdown: pass through (decoded as UTF-8)
    """
    if mime_type == "application/pdf" or filename.lower().endswith(".pdf"):
        return _parse_pdf(content)
    elif mime_type in ("text/html", "application/xhtml+xml") or \
            filename.lower().endswith((".html", ".htm")):
        return _parse_html(content)
    else:
        # Plain text or markdown — decode and return
        return content.decode("utf-8", errors="replace")


def _parse_pdf(content: bytes) -> str:
    """Extract text from PDF bytes."""
    try:
        import pdfplumber
        import io
        pages = []
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            for i, page in enumerate(pdf.pages):
                text = page.extract_text() or ""
                if text.strip():
                    pages.append(f"## Page {i + 1}\n\n{text}")
        if pages:
            return "\n\n".join(pages)
    except ImportError:
        logger.info("pdfplumber not available, trying PyPDF2")
    except Exception as e:
        logger.warning("pdfplumber failed: %s, trying PyPDF2", e)

    try:
        from PyPDF2 import PdfReader
        import io
        reader = PdfReader(io.BytesIO(content))
        pages = []
        for i, page in enumerate(reader.pages):
            text = page.extract_text() or ""
            if text.strip():
                pages.append(f"## Page {i + 1}\n\n{text}")
        return "\n\n".join(pages) if pages else "(Empty PDF)"
    except ImportError:
        raise ImportError(
            "PDF parsing requires pdfplumber or PyPDF2. "
            "Install with: uv pip install pdfplumber"
        )


def _parse_html(content: bytes) -> str:
    """Extract readable text from HTML bytes using trafilatura."""
    try:
        import trafilatura
        text = trafilatura.extract(
            content.decode("utf-8", errors="replace"),
            include_comments=False,
            include_tables=True,
        )
        if text:
            return text
    except Exception as e:
        logger.warning("trafilatura failed: %s", e)

    # Fallback: strip HTML tags
    html_text = content.decode("utf-8", errors="replace")
    return re.sub(r"<[^>]+>", "", html_text).strip()


async def fetch_url_content(url: str) -> tuple[bytes, str, str]:
    """Fetch content from a URL. Returns (content_bytes, filename, mime_type)."""
    async with httpx.AsyncClient(
        timeout=URL_FETCH_TIMEOUT, follow_redirects=True
    ) as client:
        response = await client.get(url)
        response.raise_for_status()
        content_type = response.headers.get("content-type", "text/html")
        mime_type = content_type.split(";")[0].strip()
        # Derive filename from URL
        from urllib.parse import urlparse
        path = urlparse(url).path
        filename = path.split("/")[-1] or "document"
        if not filename.endswith((".pdf", ".html", ".htm", ".txt", ".md")):
            if "pdf" in mime_type:
                filename += ".pdf"
            elif "html" in mime_type:
                filename += ".html"
        return response.content, filename, mime_type


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
