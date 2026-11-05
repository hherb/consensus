"""kg_query handler and embedder failure contracts (issue #86).

The tool used to advertise a ``path`` mode that answered "not yet implemented"
as a *successful* result, and its failure branches returned errors without
logging them (golden rule 6).
"""

import logging

import httpx
import pytest

from consensus.tools import ToolContext
from consensus.tools_memory import (
    _KG_QUERY_SCHEMA, KG_QUERY_MODES, EmbeddingClient, MemoryUnavailableError,
    _kg_query_handler,
)

CTX = ToolContext(caller_entity_id=1, discussion_id=1)


async def _query(db, **arguments):
    """Run kg_query; the embed client is unused by the modes exercised here."""
    return await _kg_query_handler(arguments, CTX, db, embed_client=None)


def test_schema_advertises_only_implemented_modes() -> None:
    """The schema enum and the handler agree on the mode set."""
    assert _KG_QUERY_SCHEMA["properties"]["mode"]["enum"] == list(KG_QUERY_MODES)
    assert "path" not in KG_QUERY_MODES
    assert "target" not in _KG_QUERY_SCHEMA["properties"]


async def test_unknown_mode_is_an_error_not_a_silent_search(tmp_db) -> None:
    """A mode outside the enum (e.g. the removed 'path') is rejected."""
    result = await _query(tmp_db, query="free will", mode="path")
    assert result.is_error
    assert "neighbors" in result.content


async def test_neighbors_lists_edges_both_directions(tmp_db) -> None:
    """neighbors mode reports outgoing and incoming edges of the node."""
    tmp_db.upsert_kg_node("a", "free will", "concept")
    tmp_db.upsert_kg_node("b", "determinism", "concept")
    tmp_db.upsert_kg_node("c", "compatibilism", "concept")
    tmp_db.add_kg_edge("e1", "a", "b", "contradicts")
    tmp_db.add_kg_edge("e2", "c", "a", "reconciles")

    result = await _query(tmp_db, query="free will", mode="neighbors")

    assert not result.is_error
    assert "free will --[contradicts]--> determinism" in result.content
    assert "free will <--[reconciles]-- compatibilism" in result.content
    assert result.metadata == {"count": 2}


async def test_neighbors_unknown_label_is_not_an_error(tmp_db) -> None:
    """A missing node is a normal answer pointing at search mode."""
    result = await _query(tmp_db, query="nothing", mode="neighbors")
    assert not result.is_error
    assert "mode='search'" in result.content


async def test_neighbors_db_failure_is_logged_and_reported(
    tmp_db, monkeypatch, caplog,
) -> None:
    """A DB fault in the label lookup is caught, logged and returned as an error."""
    def boom(label):
        raise RuntimeError("disk I/O error")

    monkeypatch.setattr(tmp_db, "get_kg_node_by_label", boom)
    with caplog.at_level(logging.ERROR, logger="consensus.tools_memory"):
        result = await _query(tmp_db, query="free will", mode="neighbors")

    assert result.is_error
    assert "disk I/O error" in result.content
    [rec] = [r for r in caplog.records if "Knowledge graph query failed" in r.getMessage()]
    assert rec.exc_info is not None


async def test_search_embedder_outage_is_a_warning_without_traceback(
    tmp_db, caplog,
) -> None:
    """An unreachable embedder is an expected outage: warning, no traceback, error result."""
    class DownEmbedder:
        async def embed(self, text):
            raise MemoryUnavailableError("connection refused")

    with caplog.at_level(logging.WARNING, logger="consensus.tools_memory"):
        result = await _kg_query_handler(
            {"query": "free will", "mode": "search"}, CTX, tmp_db, DownEmbedder(),
        )

    assert result.is_error
    assert "Memory service unavailable: connection refused" in result.content
    [rec] = [r for r in caplog.records if "Memory service unavailable" in r.getMessage()]
    assert rec.levelno == logging.WARNING
    assert rec.exc_info is None


async def test_malformed_embedding_response_keeps_its_traceback(
    tmp_db, monkeypatch, caplog,
) -> None:
    """A response without 'embedding' is a bug/misconfig, logged with a traceback."""
    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"oops": 1}))
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: real_client(transport=transport, **kw),
    )

    with caplog.at_level(logging.ERROR, logger="consensus.tools_memory"):
        with pytest.raises(MemoryUnavailableError):
            await EmbeddingClient(tmp_db).embed("hello")

    [rec] = [r for r in caplog.records if "Unexpected embedding failure" in r.getMessage()]
    assert rec.exc_info is not None
    assert rec.exc_info[0] is KeyError
