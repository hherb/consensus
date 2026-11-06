"""Characterization tests for the institutional-memory tool handlers.

Written before ``tools_memory`` was split into a package (issue #61). Every
handler is driven through the public path production uses —
``create_memory_provider(db).execute(name, arguments, context)`` — against a
real database, with the embedding client (the network edge) replaced where
the provider factory looks it up. That keeps these tests valid across the
split without naming submodules that do not exist yet.
"""

import asyncio
import logging

import pytest

from consensus import background
from consensus.tools import ToolContext
from consensus.tools_memory import (
    MemoryUnavailableError, _pack_embedding, create_memory_provider,
)
from consensus.tools_memory.handlers import _index_messages
from .document_helpers import FakeEmbedClient, patch_where_defined

LOGGER = "consensus.tools_memory"

TOOL_NAMES = {
    "memory_store", "memory_recall", "memory_forget",
    "discussion_search", "kg_assert", "kg_query",
}


async def drain_background() -> None:
    """Wait for every background task the handlers spawned."""
    while background._background_tasks:
        await asyncio.gather(*list(background._background_tasks))


@pytest.fixture
def embedder(monkeypatch):
    """The embed client every provider built in this test will use."""
    client = FakeEmbedClient(vector=[1.0, 0.0])
    patch_where_defined(
        monkeypatch, create_memory_provider, "EmbeddingClient", lambda db: client,
    )
    return client


@pytest.fixture
def discussion(tmp_db, sample_ai_entity):
    """A discussion id with the sample AI entity as its moderator."""
    return tmp_db.create_discussion("Free will", sample_ai_entity)


@pytest.fixture
def ctx(sample_ai_entity, discussion):
    return ToolContext(caller_entity_id=sample_ai_entity, discussion_id=discussion)


@pytest.fixture
def run(tmp_db, embedder, ctx):
    """Execute a memory tool by name through the real provider."""
    provider = create_memory_provider(tmp_db)

    async def _run(name, context=None, **arguments):
        return await provider.execute(name, arguments, context or ctx)

    return _run


def _boom(*args, **kwargs):
    raise RuntimeError("disk I/O error")


def _failure_record(caplog, prefix):
    [rec] = [r for r in caplog.records if r.getMessage().startswith(prefix)]
    return rec


class TestProvider:
    async def test_registers_the_six_tools_with_schemas(self, tmp_db, embedder):
        provider = create_memory_provider(tmp_db)
        tools = {t.name: t for t in await provider.list_tools()}
        assert set(tools) == TOOL_NAMES
        for tool in tools.values():
            assert tool.parameters["type"] == "object"
            assert tool.parameters["required"]
            assert tool.description

    def test_one_embed_client_is_built_from_the_db(self, tmp_db, monkeypatch):
        built = []
        patch_where_defined(
            monkeypatch, create_memory_provider, "EmbeddingClient",
            lambda db: built.append(db) or FakeEmbedClient(),
        )
        create_memory_provider(tmp_db)
        assert built == [tmp_db]


class TestMemoryStore:
    async def test_empty_content_is_an_error(self, run):
        result = await run("memory_store", content="   ")
        assert result.is_error
        assert result.content == "No content provided."

    async def test_stores_and_embeds_in_background(
        self, run, tmp_db, sample_ai_entity, discussion, embedder,
    ):
        result = await run("memory_store", content="  determinism wins  ")
        await drain_background()

        assert not result.is_error
        memory_id = result.metadata["memory_id"]
        assert result.content == f"Memory stored (id: {memory_id})."
        assert embedder.calls == ["determinism wins"]
        [row] = tmp_db.get_entity_memories_with_embeddings(sample_ai_entity)
        assert row["id"] == memory_id
        assert row["content"] == "determinism wins"
        stored = tmp_db.conn.execute(
            "SELECT discussion_id FROM entity_memories WHERE id=?", (memory_id,),
        ).fetchone()
        assert stored[0] == discussion

    async def test_no_discussion_is_stored_as_null(self, run, tmp_db, sample_ai_entity):
        context = ToolContext(caller_entity_id=sample_ai_entity, discussion_id=0)
        result = await run("memory_store", context=context, content="x")
        await drain_background()
        stored = tmp_db.conn.execute(
            "SELECT discussion_id FROM entity_memories WHERE id=?",
            (result.metadata["memory_id"],),
        ).fetchone()
        assert stored[0] is None

    async def test_db_failure_is_logged_and_reported(
        self, run, tmp_db, monkeypatch, caplog,
    ):
        monkeypatch.setattr(tmp_db, "add_entity_memory", _boom)
        with caplog.at_level(logging.ERROR, logger=LOGGER):
            result = await run("memory_store", content="x")
        assert result.is_error
        assert result.content == "Failed to store memory: disk I/O error"
        assert _failure_record(caplog, "Failed to store memory").exc_info

    @pytest.mark.parametrize("error, expected", [
        (MemoryUnavailableError("down"), "Could not embed memory"),
        (RuntimeError("bad vector"), "Unexpected error embedding memory"),
    ])
    async def test_embed_failure_keeps_memory_and_logs(
        self, run, tmp_db, sample_ai_entity, embedder, caplog, error, expected,
    ):
        embedder.error = error
        with caplog.at_level(logging.WARNING, logger=LOGGER):
            result = await run("memory_store", content="x")
            await drain_background()
        assert not result.is_error
        assert tmp_db.get_entity_memories_with_embeddings(sample_ai_entity) == []
        assert any(r.getMessage().startswith(expected) for r in caplog.records)


class TestMemoryRecall:
    async def _remember(self, tmp_db, entity, memory_id, content, vector):
        tmp_db.add_entity_memory(memory_id, entity, content)
        tmp_db.set_entity_memory_embedding(memory_id, _pack_embedding(vector))

    async def test_empty_query_is_an_error(self, run):
        result = await run("memory_recall", query="")
        assert result.is_error

    async def test_nothing_stored(self, run):
        result = await run("memory_recall", query="anything")
        assert not result.is_error
        assert result.content == "No memories stored yet."

    async def test_ranks_own_memories_and_applies_limit(
        self, run, tmp_db, sample_ai_entity, sample_human_entity,
    ):
        await self._remember(tmp_db, sample_ai_entity, "m-far", "far", [0.0, 1.0])
        await self._remember(tmp_db, sample_ai_entity, "m-near", "near", [1.0, 0.0])
        await self._remember(tmp_db, sample_ai_entity, "m-mid", "mid", [1.0, 1.0])
        await self._remember(tmp_db, sample_human_entity, "m-other", "other", [1.0, 0.0])

        result = await run("memory_recall", query="q", limit=2)

        assert result.content == (
            "Recalled 2 memories for 'q':\n\n1. [m-near] near\n2. [m-mid] mid"
        )
        assert result.metadata == {"count": 2}

    async def test_embedder_down_is_an_error(self, run, embedder, caplog):
        embedder.error = MemoryUnavailableError("down")
        with caplog.at_level(logging.WARNING, logger=LOGGER):
            result = await run("memory_recall", query="q")
        assert result.is_error
        assert result.content == "Memory service unavailable: down"
        # An expected outage logs without a traceback.
        assert not _failure_record(caplog, "Memory service unavailable").exc_info

    async def test_db_failure_is_an_error(self, run, tmp_db, monkeypatch):
        monkeypatch.setattr(tmp_db, "get_entity_memories_with_embeddings", _boom)
        result = await run("memory_recall", query="q")
        assert result.is_error
        assert result.content == "Failed to retrieve memories: disk I/O error"


class TestMemoryForget:
    async def test_empty_id_is_an_error(self, run):
        result = await run("memory_forget", memory_id=" ")
        assert result.is_error

    async def test_deletes_own_memory(self, run, tmp_db, sample_ai_entity):
        tmp_db.add_entity_memory("m1", sample_ai_entity, "x")
        result = await run("memory_forget", memory_id="m1")
        assert not result.is_error
        assert result.content == "Memory m1 deleted."
        assert tmp_db.conn.execute("SELECT COUNT(*) FROM entity_memories").fetchone()[0] == 0

    async def test_cannot_delete_another_entitys_memory(
        self, run, tmp_db, sample_human_entity,
    ):
        tmp_db.add_entity_memory("m1", sample_human_entity, "x")
        result = await run("memory_forget", memory_id="m1")
        assert result.is_error
        assert "not found or does not belong" in result.content
        assert tmp_db.conn.execute("SELECT COUNT(*) FROM entity_memories").fetchone()[0] == 1

    async def test_db_failure_is_an_error(self, run, tmp_db, monkeypatch):
        monkeypatch.setattr(tmp_db, "delete_entity_memory", _boom)
        result = await run("memory_forget", memory_id="m1")
        assert result.is_error
        assert result.content == "Failed to delete memory: disk I/O error"


class TestDiscussionSearch:
    def _embedded_message(self, tmp_db, disc, entity, content, vector):
        msg_id = tmp_db.add_message(disc, entity, content, "participant")
        tmp_db.set_message_embedding(str(msg_id), _pack_embedding(vector))
        return msg_id

    async def test_empty_query_is_an_error(self, run):
        result = await run("discussion_search", query="")
        assert result.is_error

    async def test_lazily_indexes_the_current_discussion(
        self, run, tmp_db, discussion, sample_ai_entity, embedder,
    ):
        tmp_db.add_message(discussion, sample_ai_entity, "unindexed", "participant")

        first = await run("discussion_search", query="q")
        await drain_background()

        # The search ran before the background pass finished.
        assert first.content == "No indexed discussion messages found yet."
        assert tmp_db.get_unindexed_message_ids(discussion) == []
        assert embedder.calls == ["q", "unindexed"]

        second = await run("discussion_search", query="q")
        assert "1. [Discussion: Free will]\n   unindexed" in second.content

    async def test_ranks_truncates_and_reports_topic(
        self, run, tmp_db, discussion, sample_ai_entity,
    ):
        long_text = "a" * 400
        self._embedded_message(tmp_db, discussion, sample_ai_entity, long_text, [1.0, 0.0])
        self._embedded_message(tmp_db, discussion, sample_ai_entity, "off", [0.0, 1.0])

        result = await run("discussion_search", query="q", limit=1)

        assert result.content == (
            "Found 1 relevant passages for 'q':\n\n"
            f"1. [Discussion: Free will]\n   {'a' * 300}"
        )
        assert result.metadata == {"count": 1}

    async def test_topic_filter_limits_discussions(
        self, run, tmp_db, discussion, sample_ai_entity,
    ):
        other = tmp_db.create_discussion("Climate policy", sample_ai_entity)
        self._embedded_message(tmp_db, discussion, sample_ai_entity, "fw", [1.0, 0.0])
        self._embedded_message(tmp_db, other, sample_ai_entity, "cp", [1.0, 0.0])

        result = await run("discussion_search", query="q", topic_filter="Climate")

        assert "cp" in result.content
        assert "fw" not in result.content

    async def test_embedder_down_is_an_error(self, run, embedder):
        embedder.error = MemoryUnavailableError("down")
        result = await run("discussion_search", query="q")
        assert result.is_error
        assert result.content == "Memory service unavailable: down"

    async def test_db_failure_is_an_error(self, run, tmp_db, monkeypatch):
        monkeypatch.setattr(tmp_db, "get_messages_with_embeddings", _boom)
        result = await run("discussion_search", query="q")
        assert result.is_error
        assert result.content == "Failed to search discussions: disk I/O error"

    async def test_index_check_failure_is_logged_and_search_proceeds(
        self, run, tmp_db, monkeypatch, caplog,
    ):
        monkeypatch.setattr(tmp_db, "get_unindexed_message_ids", _boom)
        with caplog.at_level(logging.WARNING, logger=LOGGER):
            result = await run("discussion_search", query="q")
        assert not result.is_error
        assert any("unindexed messages" in r.getMessage() for r in caplog.records)

    async def test_in_flight_pass_is_not_duplicated(
        self, run, tmp_db, discussion, sample_ai_entity, embedder,
    ):
        tmp_db.add_message(discussion, sample_ai_entity, "m", "participant")
        await run("discussion_search", query="q1")
        await run("discussion_search", query="q2")  # pass from q1 still pending
        await drain_background()
        assert embedder.calls.count("m") == 1


class TestIndexMessages:
    async def test_embeds_truncated_content_and_skips_empty(
        self, tmp_db, discussion, sample_ai_entity,
    ):
        long_id = tmp_db.add_message(discussion, sample_ai_entity, "b" * 1500, "participant")
        empty_id = tmp_db.add_message(discussion, sample_ai_entity, "", "participant")
        client = FakeEmbedClient()

        await _index_messages([str(long_id), str(empty_id)], tmp_db, client, discussion)

        assert client.calls == ["b" * 1000]
        assert tmp_db.get_unindexed_message_ids(discussion) == [str(empty_id)]

    async def test_stops_when_embedder_goes_down_and_says_so(
        self, tmp_db, discussion, sample_ai_entity, caplog,
    ):
        ids = [
            str(tmp_db.add_message(discussion, sample_ai_entity, text, "participant"))
            for text in ("one", "two", "three")
        ]
        client = FakeEmbedClient(errors_by_text={"two": MemoryUnavailableError("down")})

        with caplog.at_level(logging.WARNING, logger=LOGGER):
            await _index_messages(ids, tmp_db, client, discussion)

        assert client.calls == ["one", "two"]
        [rec] = [r for r in caplog.records if "stopped after" in r.getMessage()]
        assert "stopped after 1/3 message(s)" in rec.getMessage()

    async def test_continues_past_a_single_bad_message(
        self, tmp_db, discussion, sample_ai_entity, caplog,
    ):
        ids = [
            str(tmp_db.add_message(discussion, sample_ai_entity, text, "participant"))
            for text in ("one", "two")
        ]
        client = FakeEmbedClient(errors_by_text={"one": RuntimeError("bad")})

        with caplog.at_level(logging.WARNING, logger=LOGGER):
            await _index_messages(ids, tmp_db, client, discussion)

        assert client.calls == ["one", "two"]
        assert tmp_db.get_unindexed_message_ids(discussion) == [ids[0]]
        assert any("Failed to index message" in r.getMessage() for r in caplog.records)


class TestKgAssert:
    @pytest.mark.parametrize("missing", ["subject", "relation", "object"])
    async def test_all_three_parts_required(self, run, missing):
        arguments = {"subject": "a", "relation": "r", "object": "b"}
        arguments[missing] = "  "
        result = await run("kg_assert", **arguments)
        assert result.is_error
        assert result.content == "subject, relation, and object are all required."

    async def test_creates_nodes_edge_and_embeddings(
        self, run, tmp_db, discussion, embedder,
    ):
        result = await run(
            "kg_assert", subject="free will", relation="contradicts",
            object="determinism", description="  classic tension ",
        )
        await drain_background()

        assert result.content == "Asserted: free will --[contradicts]--> determinism"
        assert result.metadata == {
            "subject": "free will", "relation": "contradicts", "object": "determinism",
        }
        subj = tmp_db.get_kg_node_by_label("free will")
        obj = tmp_db.get_kg_node_by_label("determinism")
        assert subj["description"] == "classic tension"
        assert obj["description"] is None
        [edge] = tmp_db.get_kg_neighbors(subj["id"])
        assert (edge["label"], edge["relation"], edge["direction"]) == (
            "determinism", "contradicts", "out",
        )
        edge_disc = tmp_db.conn.execute("SELECT discussion_id FROM kg_edges").fetchone()
        assert edge_disc[0] == discussion
        assert embedder.calls == ["free will", "determinism"]
        assert len(tmp_db.get_kg_nodes_with_embeddings()) == 2

    async def test_reuses_existing_nodes(self, run, tmp_db):
        tmp_db.upsert_kg_node("n1", "free will", "concept", "kept")
        await run("kg_assert", subject="free will", relation="r", object="x")
        await run("kg_assert", subject="x", relation="s", object="free will")
        await drain_background()

        assert tmp_db.conn.execute("SELECT COUNT(*) FROM kg_nodes").fetchone()[0] == 2
        assert tmp_db.get_kg_node_by_label("free will")["description"] == "kept"
        assert len(tmp_db.get_kg_neighbors("n1")) == 2

    async def test_db_failure_is_an_error(self, run, tmp_db, monkeypatch):
        monkeypatch.setattr(tmp_db, "add_kg_edge", _boom)
        result = await run("kg_assert", subject="a", relation="r", object="b")
        assert result.is_error
        assert result.content == "Failed to assert knowledge triple: disk I/O error"

    async def test_embedder_down_stops_node_embedding_and_says_so(
        self, run, tmp_db, embedder, caplog,
    ):
        embedder.error = MemoryUnavailableError("down")
        with caplog.at_level(logging.WARNING, logger=LOGGER):
            result = await run("kg_assert", subject="a", relation="r", object="b")
            await drain_background()

        assert not result.is_error
        assert embedder.calls == ["a"]  # gave up after the first outage
        assert tmp_db.get_kg_nodes_with_embeddings() == []
        assert any("Could not embed kg nodes" in r.getMessage() for r in caplog.records)

    async def test_bad_node_embedding_is_logged_and_next_node_proceeds(
        self, run, tmp_db, embedder, caplog,
    ):
        embedder.errors_by_text = {"a": RuntimeError("bad")}
        with caplog.at_level(logging.WARNING, logger=LOGGER):
            await run("kg_assert", subject="a", relation="r", object="b")
            await drain_background()

        assert embedder.calls == ["a", "b"]
        assert [n["label"] for n in tmp_db.get_kg_nodes_with_embeddings()] == ["b"]
        assert any("Failed to embed kg node" in r.getMessage() for r in caplog.records)


class TestKgQuerySearch:
    """The ``neighbors`` mode is covered by ``test_tools_memory_kg_query.py``."""

    def _node(self, tmp_db, node_id, label, vector, description=None):
        tmp_db.upsert_kg_node(node_id, label, "concept", description)
        tmp_db.set_kg_node_embedding(node_id, _pack_embedding(vector))

    async def test_empty_graph(self, run):
        result = await run("kg_query", query="q")
        assert not result.is_error
        assert result.content == "Knowledge graph is empty."

    async def test_ranks_nodes_and_shows_descriptions(self, run, tmp_db):
        self._node(tmp_db, "a", "near", [1.0, 0.0], "close match")
        self._node(tmp_db, "b", "far", [0.0, 1.0])

        result = await run("kg_query", query="q")

        assert result.content == (
            "Knowledge graph nodes related to 'q':\n\n"
            "1. near — close match\n2. far"
        )
        assert result.metadata == {"count": 2}

    async def test_returns_at_most_the_default_limit(self, run, tmp_db):
        from consensus.tools_memory.constants import SEARCH_DEFAULT_LIMIT

        for i in range(SEARCH_DEFAULT_LIMIT + 2):
            self._node(tmp_db, f"n{i}", f"node {i}", [1.0, float(i)])
        result = await run("kg_query", query="q")
        assert result.metadata == {"count": SEARCH_DEFAULT_LIMIT}

    async def test_embedder_down_is_an_error(self, run, embedder):
        embedder.error = MemoryUnavailableError("down")
        result = await run("kg_query", query="q")
        assert result.is_error
        assert result.content == "Memory service unavailable: down"

    async def test_db_failure_is_an_error(self, run, tmp_db, monkeypatch):
        monkeypatch.setattr(tmp_db, "get_kg_nodes_with_embeddings", _boom)
        result = await run("kg_query", query="q")
        assert result.is_error
        assert result.content == "Knowledge graph query failed: disk I/O error"

    async def test_empty_query_is_an_error(self, run):
        result = await run("kg_query", query=" ", mode="search")
        assert result.is_error

    async def test_neighbors_of_an_unconnected_node(self, run, tmp_db):
        tmp_db.upsert_kg_node("a", "lonely", "concept")
        result = await run("kg_query", query="lonely", mode="neighbors")
        assert not result.is_error
        assert result.content == "Node 'lonely' has no connections yet."


class TestLazyIndexingMarker:
    """Both lazy-indexing call sites share one in-flight marker.

    The semantic context strategy used to add the *bare* discussion id to
    the marker set while ``_index_messages`` discarded the *scoped* key, so
    the bare id was never removed: after the first pass, that discussion's
    later messages were never indexed for the life of the process. Its task
    was also an untracked ``create_task``.
    """

    async def test_semantic_context_reindexes_later_messages(
        self, tmp_db, discussion, sample_ai_entity,
    ):
        from consensus.context_strategies import _load_semantic

        client = FakeEmbedClient(vector=[1.0, 0.0])
        tmp_db.add_message(discussion, sample_ai_entity, "first", "participant")
        await _load_semantic(tmp_db, discussion, 8, client)
        await drain_background()
        assert tmp_db.get_unindexed_message_ids(discussion) == []

        tmp_db.add_message(discussion, sample_ai_entity, "second", "participant")
        await _load_semantic(tmp_db, discussion, 8, client)
        await drain_background()
        assert tmp_db.get_unindexed_message_ids(discussion) == []

    async def test_same_discussion_id_in_another_database_is_not_blocked(
        self, tmp_db, tmp_path, discussion, sample_ai_entity,
    ):
        from consensus.database import Database
        from consensus.tools_memory import ensure_discussion_indexed

        other_db = Database(str(tmp_path / "other.db"))
        try:
            other_disc = other_db.create_discussion("Other session")
            assert other_disc == discussion  # every session's first id collides
            tmp_db.add_message(discussion, sample_ai_entity, "a", "participant")
            bob = other_db.add_entity("Bob", "human", "#00ff00")
            other_db.add_message(other_disc, bob, "b", "participant")
            client = FakeEmbedClient()

            assert ensure_discussion_indexed(tmp_db, client, discussion)
            # tmp_db's pass is still pending; the other session must not wait on it.
            assert ensure_discussion_indexed(other_db, client, other_disc)
            # A second request for an in-flight discussion starts nothing.
            assert not ensure_discussion_indexed(tmp_db, client, discussion)
            await drain_background()

            assert sorted(client.calls) == ["a", "b"]
            # Nothing pending once indexed, so nothing starts.
            assert not ensure_discussion_indexed(tmp_db, client, discussion)
        finally:
            other_db.conn.close()
