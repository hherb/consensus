"""Characterization tests for the memory embedding client and vector helpers.

Written before ``tools_memory`` was split into a package (issue #61): the
module was at 45% coverage, so a green suite proved nothing about a move.
The only fake is the network edge — ``httpx`` is driven through a
``MockTransport`` — and the database is real.
"""

import asyncio
import logging

import httpx
import pytest

from consensus.tools_memory import constants
from consensus.models import DEFAULT_EMBEDDING_ENDPOINT, DEFAULT_EMBEDDING_MODEL
from consensus.tools_memory import (
    EmbeddingClient, EmbeddingContextLengthError, MemoryUnavailableError,
    _pack_embedding, _rank_by_similarity,
)

LOGGER = "consensus.tools_memory"


@pytest.fixture
def sleeps(monkeypatch):
    """Record backoff delays instead of sleeping through them."""
    recorded: list[float] = []

    async def fake_sleep(delay):
        recorded.append(delay)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    return recorded


@pytest.fixture
def transport(monkeypatch):
    """Route every ``httpx.AsyncClient`` through a scripted handler.

    Returns a list of requests seen; set ``.responder`` on the returned
    object to a callable ``request -> httpx.Response`` (or raise).
    """
    real_client = httpx.AsyncClient

    class Script:
        def __init__(self) -> None:
            self.requests: list[httpx.Request] = []
            self.responder = lambda request: httpx.Response(
                200, json={"embedding": [0.5, 0.5]},
            )
            self.timeouts: list = []

        def handle(self, request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return self.responder(request)

    script = Script()

    def factory(*args, **kwargs):
        script.timeouts.append(kwargs.get("timeout"))
        return real_client(
            *args, transport=httpx.MockTransport(script.handle), **kwargs,
        )

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    return script


def _responses(*responses):
    """Return a responder yielding each response (or raising each exception) in turn."""
    queue = list(responses)

    def responder(request):
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    return responder


class TestEmbed:
    async def test_posts_prompt_to_configured_endpoint(self, tmp_db, transport):
        tmp_db.set_memory_config("embedding_endpoint", "http://embed.test:1234/")
        tmp_db.set_memory_config("embedding_model", "my-model")

        vec = await EmbeddingClient(tmp_db).embed("hello")

        assert vec == [0.5, 0.5]
        [request] = transport.requests
        # The trailing slash is stripped, not doubled.
        assert str(request.url) == "http://embed.test:1234/api/embeddings"
        assert request.read() == b'{"model":"my-model","prompt":"hello"}'
        assert transport.timeouts == [constants.EMBED_TIMEOUT]

    async def test_unreadable_config_falls_back_to_defaults(
        self, tmp_db, transport, monkeypatch,
    ):
        def boom():
            raise RuntimeError("no such table")

        monkeypatch.setattr(tmp_db, "get_memory_config", boom)
        await EmbeddingClient(tmp_db).embed("x")

        [request] = transport.requests
        assert str(request.url) == (
            DEFAULT_EMBEDDING_ENDPOINT.rstrip("/") + "/api/embeddings"
        )
        assert DEFAULT_EMBEDDING_MODEL.encode() in request.read()

    async def test_connect_error_is_unavailable_without_retry(
        self, tmp_db, transport, sleeps,
    ):
        transport.responder = _responses(httpx.ConnectError("refused"))

        with pytest.raises(MemoryUnavailableError, match="Cannot connect"):
            await EmbeddingClient(tmp_db).embed("x")
        assert len(transport.requests) == 1
        assert sleeps == []

    async def test_client_error_is_unavailable_without_retry(
        self, tmp_db, transport, sleeps,
    ):
        transport.responder = _responses(httpx.Response(404, text="no model"))

        with pytest.raises(MemoryUnavailableError, match="Embedding request failed"):
            await EmbeddingClient(tmp_db).embed("x")
        assert len(transport.requests) == 1
        assert sleeps == []

    async def test_context_length_500_is_not_retried(
        self, tmp_db, transport, sleeps,
    ):
        transport.responder = _responses(httpx.Response(
            500, json={"error": "input exceeds the context length"},
        ))

        with pytest.raises(EmbeddingContextLengthError, match="context length"):
            await EmbeddingClient(tmp_db).embed("x")
        assert len(transport.requests) == 1
        assert sleeps == []

    @pytest.mark.parametrize("failure", [
        httpx.Response(500, text="not json"),
        httpx.Response(500, json={"detail": "no error field"}),
        httpx.Response(503, json={"error": "overloaded"}),
        httpx.ReadTimeout("slow"),
    ], ids=["500-text", "500-json-no-error", "503", "timeout"])
    async def test_transient_failure_is_retried_then_succeeds(
        self, tmp_db, transport, sleeps, failure, caplog,
    ):
        transport.responder = _responses(
            failure, httpx.Response(200, json={"embedding": [1.0]}),
        )

        with caplog.at_level(logging.WARNING, logger=LOGGER):
            vec = await EmbeddingClient(tmp_db).embed("x")

        assert vec == [1.0]
        assert len(transport.requests) == 2
        assert sleeps == [constants.EMBED_BASE_DELAY]
        assert any("retrying" in r.getMessage() for r in caplog.records)

    async def test_persistent_failure_backs_off_exponentially_then_gives_up(
        self, tmp_db, transport, sleeps,
    ):
        transport.responder = lambda request: httpx.Response(503)

        with pytest.raises(MemoryUnavailableError, match="after 3 attempts"):
            await EmbeddingClient(tmp_db).embed("x")

        assert len(transport.requests) == constants.EMBED_MAX_RETRIES
        base = constants.EMBED_BASE_DELAY
        # No sleep after the final attempt: nothing follows it but the raise.
        assert sleeps == [base, base * 2]

    async def test_other_transport_error_is_unavailable_without_retry(
        self, tmp_db, transport, sleeps,
    ):
        transport.responder = _responses(httpx.ReadError("reset"))

        with pytest.raises(MemoryUnavailableError, match="reset"):
            await EmbeddingClient(tmp_db).embed("x")
        assert sleeps == []

    async def test_malformed_body_is_unavailable_and_logged_with_traceback(
        self, tmp_db, transport, caplog,
    ):
        transport.responder = _responses(httpx.Response(200, json={"vec": []}))

        with caplog.at_level(logging.ERROR, logger=LOGGER):
            with pytest.raises(MemoryUnavailableError, match="embedding"):
                await EmbeddingClient(tmp_db).embed("x")

        [rec] = [r for r in caplog.records if "Unexpected embedding failure" in r.getMessage()]
        assert rec.exc_info is not None


class TestConnectionCheck:
    async def test_reports_dimension_on_success(self, tmp_db, transport):
        ok, message = await EmbeddingClient(tmp_db).test_connection()
        assert ok
        assert message == "Connected. Embedding dim: 2"

    async def test_reports_failure_message(self, tmp_db, transport):
        transport.responder = _responses(httpx.ConnectError("refused"))
        ok, message = await EmbeddingClient(tmp_db).test_connection()
        assert not ok
        assert "Cannot connect" in message


class TestVectorHelpers:
    def test_pack_round_trips_through_float32(self):
        from consensus.tools_memory.embedding import _unpack_embedding

        blob = _pack_embedding([0.25, -1.5, 3.0])
        assert len(blob) == 12
        assert _unpack_embedding(blob) == [0.25, -1.5, 3.0]

    @pytest.mark.parametrize("a, b, expected", [
        ([1.0, 0.0], [1.0, 0.0], 1.0),
        ([1.0, 0.0], [0.0, 1.0], 0.0),
        ([1.0, 0.0], [-1.0, 0.0], -1.0),
        ([1.0, 0.0], [1.0, 0.0, 0.0], 0.0),   # dimension mismatch
        ([0.0, 0.0], [1.0, 0.0], 0.0),        # zero vector
        ([1.0, 0.0], [0.0, 0.0], 0.0),
    ])
    def test_cosine_similarity(self, a, b, expected):
        from consensus.tools_memory.embedding import _cosine_similarity

        assert _cosine_similarity(a, b) == pytest.approx(expected)

    def test_rank_orders_by_similarity_and_applies_limit(self):
        rows = [
            {"id": "far", "embedding": _pack_embedding([0.0, 1.0])},
            {"id": "near", "embedding": _pack_embedding([1.0, 0.1])},
            {"id": "mid", "embedding": _pack_embedding([1.0, 1.0])},
        ]
        top = _rank_by_similarity([1.0, 0.0], rows, 2)
        assert [r["id"] for r in top] == ["near", "mid"]
