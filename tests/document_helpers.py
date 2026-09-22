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

    def __init__(self, base_url: str = "", api_key: str = "") -> None:
        FakeAIClient.last_init = {"base_url": base_url, "api_key": api_key}
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
    def __init__(self, text) -> None:
        self._text = text

    def extract_text(self):
        """Return the page's text, mimicking pdfplumber's API."""
        return self._text


class FakePdf:
    def __init__(self, pages) -> None:
        self.pages = pages

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> bool:
        return False


class FakeHttpResponse:
    def __init__(self, content: bytes, content_type: str) -> None:
        self.content = content
        self.headers = {"content-type": content_type}

    def raise_for_status(self) -> None:
        """No-op: these tests only exercise successful fetches."""


class FakeHttpClient:
    """Async-context-manager stand-in for ``httpx.AsyncClient``."""

    def __init__(self, response, recorder) -> None:
        self._response = response
        self._recorder = recorder

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc) -> bool:
        return False

    async def get(self, url):
        """Record the requested URL and return the canned response."""
        self._recorder.append(url)
        return self._response



def embed_all(db, document_id, vector=(1.0, 0.0)) -> None:
    """Give every chunk of *document_id* the same embedding."""
    from consensus.tools_document import embedding

    for chunk in db.get_document_chunks(document_id):
        db.set_chunk_embedding(chunk["id"], embedding._pack_embedding(list(vector)))
