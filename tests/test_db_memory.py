"""Tests for memory, embeddings, and knowledge graph operations."""

import pytest

try:
    import sqlite_vec  # noqa: F401
    HAS_SQLITE_VEC = True
except ImportError:
    HAS_SQLITE_VEC = False

from consensus.database import Database


@pytest.mark.skipif(not HAS_SQLITE_VEC, reason="sqlite_vec not installed")
class TestMemoryAndKG:
    def test_memory_config_get_set(self, tmp_db):
        config = tmp_db.get_memory_config()
        assert "embedding_backend" in config
        tmp_db.set_memory_config("test_key", "test_val")
        config = tmp_db.get_memory_config()
        assert config["test_key"] == "test_val"

    def test_add_entity_memory(self, tmp_db, sample_ai_entity):
        tmp_db.add_entity_memory("mem1", sample_ai_entity, "Remember this")
        tmp_db.set_entity_memory_embedding("mem1", b"\x00" * 16)
        memories = tmp_db.get_entity_memories_with_embeddings(sample_ai_entity)
        assert len(memories) == 1
        assert memories[0]["content"] == "Remember this"

    def test_delete_entity_memory(self, tmp_db, sample_ai_entity):
        tmp_db.add_entity_memory("mem2", sample_ai_entity, "Forget this")
        result = tmp_db.delete_entity_memory("mem2", sample_ai_entity)
        assert result is True

    def test_delete_nonexistent_memory(self, tmp_db, sample_ai_entity):
        result = tmp_db.delete_entity_memory("nope", sample_ai_entity)
        assert result is False

    def test_message_embedding(self, tmp_db, sample_ai_entity):
        did = tmp_db.create_discussion("T", sample_ai_entity)
        mid = tmp_db.add_message(did, sample_ai_entity, "test msg", "participant", 1)
        unindexed = tmp_db.get_unindexed_message_ids(did)
        assert str(mid) in unindexed
        tmp_db.set_message_embedding(str(mid), b"\x00" * 16)
        unindexed_after = tmp_db.get_unindexed_message_ids(did)
        assert str(mid) not in unindexed_after

    def test_get_message_content(self, tmp_db, sample_ai_entity):
        did = tmp_db.create_discussion("T", sample_ai_entity)
        mid = tmp_db.add_message(did, sample_ai_entity, "hello", "participant", 1)
        assert tmp_db.get_message_content(str(mid)) == "hello"
        assert tmp_db.get_message_content("99999") is None

    def test_get_messages_with_embeddings(self, tmp_db, sample_ai_entity):
        did = tmp_db.create_discussion("T", sample_ai_entity)
        mid = tmp_db.add_message(did, sample_ai_entity, "test", "participant", 1)
        tmp_db.set_message_embedding(str(mid), b"\x00" * 16)
        results = tmp_db.get_messages_with_embeddings()
        assert len(results) >= 1
        assert results[0]["content"] == "test"

    def test_get_messages_with_embeddings_topic_filter(self, tmp_db, sample_ai_entity):
        did = tmp_db.create_discussion("Unique Topic XYZ", sample_ai_entity)
        mid = tmp_db.add_message(did, sample_ai_entity, "test", "participant", 1)
        tmp_db.set_message_embedding(str(mid), b"\x00" * 16)
        results = tmp_db.get_messages_with_embeddings(topic_filter="Unique Topic")
        assert len(results) >= 1
        results_empty = tmp_db.get_messages_with_embeddings(topic_filter="NoMatch")
        assert len(results_empty) == 0

    def test_kg_node_upsert_and_get(self, tmp_db):
        tmp_db.upsert_kg_node("n1", "free will", "concept", "The ability to choose")
        node = tmp_db.get_kg_node_by_label("free will")
        assert node is not None
        assert node["description"] == "The ability to choose"

    def test_kg_node_not_found(self, tmp_db):
        assert tmp_db.get_kg_node_by_label("nonexistent") is None

    def test_kg_edge_and_neighbors(self, tmp_db):
        tmp_db.upsert_kg_node("n1", "A", "concept")
        tmp_db.upsert_kg_node("n2", "B", "concept")
        tmp_db.add_kg_edge("e1", "n1", "n2", "supports")
        neighbors = tmp_db.get_kg_neighbors("n1")
        assert len(neighbors) == 1
        assert neighbors[0]["label"] == "B"
        assert neighbors[0]["relation"] == "supports"
        assert neighbors[0]["direction"] == "out"
        neighbors_rev = tmp_db.get_kg_neighbors("n2")
        assert len(neighbors_rev) == 1
        assert neighbors_rev[0]["direction"] == "in"

    def test_kg_nodes_with_embeddings(self, tmp_db):
        tmp_db.upsert_kg_node("n1", "test_node", "concept")
        tmp_db.set_kg_node_embedding("n1", b"\x00" * 16)
        nodes = tmp_db.get_kg_nodes_with_embeddings()
        assert len(nodes) >= 1
        assert nodes[0]["label"] == "test_node"
