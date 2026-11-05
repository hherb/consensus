"""kg_query handler contracts (issue #86).

The tool used to advertise a ``path`` mode that answered "not yet implemented"
as a *successful* result, and its failure branches returned errors without
logging them (golden rule 6).
"""

import logging

from consensus.tools import ToolContext
from consensus.tools_memory import (
    _KG_QUERY_SCHEMA, KG_QUERY_MODES, _kg_query_handler,
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
    assert any("Knowledge graph query failed" in r.getMessage() for r in caplog.records)
