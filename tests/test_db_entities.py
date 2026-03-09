"""Tests for entity CRUD operations."""

import sqlite3

import pytest

from consensus.database import Database


class TestEntities:
    def test_add_and_get_ai_entity(self, tmp_db, sample_provider):
        eid = tmp_db.add_entity(
            "TestBot", "ai", "#ff0000", sample_provider,
            "gpt-4", 0.5, 512, "system prompt",
        )
        e = tmp_db.get_entity(eid)
        assert e["name"] == "TestBot"
        assert e["entity_type"] == "ai"
        assert e["model"] == "gpt-4"
        assert e["temperature"] == 0.5

    def test_add_and_get_human_entity(self, tmp_db):
        eid = tmp_db.add_entity("Human", "human", "#00ff00")
        e = tmp_db.get_entity(eid)
        assert e["name"] == "Human"
        assert e["entity_type"] == "human"

    def test_update_entity(self, tmp_db, sample_ai_entity):
        tmp_db.update_entity(sample_ai_entity, name="NewName", temperature=0.9)
        e = tmp_db.get_entity(sample_ai_entity)
        assert e["name"] == "NewName"
        assert e["temperature"] == 0.9

    def test_delete_entity_soft_delete(self, tmp_db, sample_ai_entity):
        result = tmp_db.delete_entity(sample_ai_entity)
        entities = tmp_db.get_entities()
        ids = [e["id"] for e in entities]
        assert sample_ai_entity not in ids

    def test_reactivate_entity(self, tmp_db, sample_ai_entity):
        did = tmp_db.create_discussion("T", sample_ai_entity)
        tmp_db.add_discussion_member(did, sample_ai_entity, True, False)
        result = tmp_db.delete_entity(sample_ai_entity)
        assert result == {"deactivated": True}
        result = tmp_db.reactivate_entity(sample_ai_entity)
        assert result is True
        entities = tmp_db.get_entities()
        ids = [e["id"] for e in entities]
        assert sample_ai_entity in ids

    def test_get_entities_returns_active_only(self, tmp_db, sample_ai_entity, sample_human_entity):
        tmp_db.delete_entity(sample_ai_entity)
        entities = tmp_db.get_entities()
        ids = [e["id"] for e in entities]
        assert sample_ai_entity not in ids
        assert sample_human_entity in ids

    def test_get_nonexistent_entity(self, tmp_db):
        assert tmp_db.get_entity(99999) is None

    def test_invalid_entity_type_rejected(self, tmp_db):
        with pytest.raises(sqlite3.IntegrityError):
            tmp_db.add_entity("Bad", "robot", "#000")

    def test_get_entities_filter_by_type(self, tmp_db, sample_ai_entity, sample_human_entity):
        ai_entities = tmp_db.get_entities(entity_type="ai")
        assert all(e["entity_type"] == "ai" for e in ai_entities)
        human_entities = tmp_db.get_entities(entity_type="human")
        assert all(e["entity_type"] == "human" for e in human_entities)

    def test_get_entities_include_inactive(self, tmp_db, sample_ai_entity):
        did = tmp_db.create_discussion("T", sample_ai_entity)
        tmp_db.add_discussion_member(did, sample_ai_entity, True, False)
        tmp_db.delete_entity(sample_ai_entity)
        entities = tmp_db.get_entities(include_inactive=True)
        ids = [e["id"] for e in entities]
        assert sample_ai_entity in ids

    def test_get_inactive_entities(self, tmp_db, sample_ai_entity):
        did = tmp_db.create_discussion("T", sample_ai_entity)
        tmp_db.add_discussion_member(did, sample_ai_entity, True, False)
        tmp_db.delete_entity(sample_ai_entity)
        inactive = tmp_db.get_inactive_entities()
        ids = [e["id"] for e in inactive]
        assert sample_ai_entity in ids


class TestExpertEntityType:
    def test_add_expert_entity(self, tmp_db, sample_provider):
        eid = tmp_db.add_entity(
            "FactChecker", "expert", "#ff0000", sample_provider,
            "gpt-4", 0.5, 512, "You are a fact checker.",
        )
        e = tmp_db.get_entity(eid)
        assert e["entity_type"] == "expert"

    def test_entity_type_enum_has_expert(self):
        from consensus.models import EntityType
        assert EntityType.EXPERT.value == "expert"
