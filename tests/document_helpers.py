"""Shared fakes for the document RAG test modules.

Every fake here stands in for a *network edge* — the embedding service, the
completion API, ``httpx``, or an optional parsing library — so that the tests
around them can run against a real :class:`~consensus.database.Database`.
"""

import sys


def patch_where_defined(monkeypatch, anchor, name, replacement) -> None:
    """Patch *name* in the module that defines *anchor*.

    ``anchor`` is a function from the module under test. Patching the module
    that actually defines it keeps these tests correct when the module is
    split into a package: a ``from .x import y`` binding lives in the
    importing module, not in the re-exporting facade, so patching the facade
    would silently have no effect.
    """
    monkeypatch.setattr(sys.modules[anchor.__module__], name, replacement)


class FakeEmbedClient:
    """Stand-in for ``tools_memory.EmbeddingClient``.

    Returns a deterministic vector per text, or raises the supplied error.
    """

    def __init__(self, vector=None, error=None, errors_by_text=None) -> None:
        self.vector = vector or [1.0, 0.0, 0.0]
        self.error = error
        self.errors_by_text = errors_by_text or {}
        self.calls: list[str] = []

    async def embed(self, text: str) -> list[float]:
        """Record the call and return (or raise) the configured result."""
        self.calls.append(text)
        if text in self.errors_by_text:
            raise self.errors_by_text[text]
        if self.error:
            raise self.error
        return list(self.vector)


class FakeResponse:
    """Minimal stand-in for an ``AIClient`` completion response."""

    def __init__(self, content: str) -> None:
        self.content = content


class FakeAIClient:
    """Records the arguments of a single ``complete()`` call."""

    last_init: dict = {}
    last_call: dict = {}
    closed = False

    def __init__(
        self, base_url: str = "", api_key: str = "", timeout: float = 0.0,
    ) -> None:
        FakeAIClient.last_init = {
            "base_url": base_url, "api_key": api_key, "timeout": timeout,
        }
        FakeAIClient.closed = False

    async def complete(self, **kwargs):
        """Return a canned completion, recording the request."""
        FakeAIClient.last_call = kwargs
        return FakeResponse("canned answer")

    async def close(self) -> None:
        """Mark the client closed so the caller's ``finally`` is observable."""
        FakeAIClient.closed = True


class FakeApp:
    """Minimal ``ConsensusApp`` surface used by the interpretation helper."""

    def __init__(self, db) -> None:
        self.db = db
        self.resolved: list[tuple] = []

    def _resolve_key_for_moderator(self, provider_id, api_key_env) -> str:
        """Record the resolution request and return a dummy key."""
        self.resolved.append((provider_id, api_key_env))
        return "resolved-key"


class FakePdfPage:
    """Stand-in for a ``pdfplumber`` page with fixed extractable text."""

    def __init__(self, text) -> None:
        self._text = text

    def extract_text(self):
        """Return the page's text, mimicking pdfplumber's API."""
        return self._text


class FakePdf:
    """Stand-in for an opened ``pdfplumber`` document (a context manager)."""

    def __init__(self, pages) -> None:
        self.pages = pages

    def __enter__(self):
        """Return self, mimicking ``pdfplumber.open``'s context manager."""
        return self

    def __exit__(self, *exc) -> None:
        """Never suppress exceptions raised inside the ``with`` block."""


def image_only_pdf_bytes() -> bytes:
    """Build a minimal, structurally valid PDF with one textless page.

    Not a fake: real ``pdfplumber`` opens this and really does extract no
    text, which is exactly what a scanned or image-only PDF looks like to
    the parser. Building the bytes here keeps the scanned-PDF test running
    against the *installed* configuration (pdfplumber present, PyPDF2
    absent) instead of a ``sys.modules`` fiction — the configuration in
    which the OCR message was unreachable (issue #78 whole-branch review).

    Returns:
        The bytes of a one-page PDF containing no text operators.
    """
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] "
        b"/Resources << >> >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref_offset = len(out)
    out += b"xref\n0 %d\n" % (len(objects) + 1)
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1, xref_offset,
    )
    return bytes(out)


class FakeHttpResponse:
    """Stand-in for a streaming ``httpx`` response.

    ``fetch_url_content`` reads bodies through ``client.stream(...)`` and
    ``aiter_bytes()`` so that an oversized body is abandoned mid-transfer
    rather than buffered first, so this fake offers the streaming surface
    as well as ``.content``.
    """

    def __init__(
        self, content: bytes, content_type: str, status_code: int = 200,
        headers: dict | None = None,
    ) -> None:
        self.content = content
        self.headers = {"content-type": content_type, **(headers or {})}
        self.status_code = status_code

    def raise_for_status(self) -> None:
        """No-op: these tests only exercise successful fetches."""

    async def aiter_bytes(self):
        """Yield the canned body as a single chunk, as httpx would."""
        yield self.content


class FakeHttpStream:
    """Async context manager returned by :meth:`FakeHttpClient.stream`."""

    def __init__(self, response) -> None:
        self._response = response

    async def __aenter__(self):
        """Hand the caller the canned response."""
        return self._response

    async def __aexit__(self, *exc) -> bool:
        """Never suppress exceptions raised inside the ``with`` block."""
        return False


class FakeHttpClient:
    """Async-context-manager stand-in for ``httpx.AsyncClient``."""

    def __init__(self, response, recorder) -> None:
        self._response = response
        self._recorder = recorder

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc) -> bool:
        return False

    def stream(self, method, url):
        """Record the requested URL and stream the canned response."""
        self._recorder.append(url)
        return FakeHttpStream(self._response)



def embed_all(db, document_id, vector=(1.0, 0.0)) -> None:
    """Give every chunk of *document_id* the same embedding."""
    from consensus.tools_document import embedding

    for chunk in db.get_document_chunks(document_id):
        db.set_chunk_embedding(chunk["id"], embedding._pack_embedding(list(vector)))
