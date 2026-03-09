"""Tests for consensus.app_entities — entity profile CRUD."""

import pytest

from consensus.app_entities import (
    delete_entity,
    get_entities,
    get_inactive_entities,
    reactivate_entity,
    save_entity,
)


class TestSaveEntity:
    def test_create_new_entity(self, tmp_db, sample_provider):
        result = save_entity(
            tmp_db, "TestBot", "ai",
            provider_id=sample_provider, model="test-model",
        )
        assert result is not None
        assert result["name"] == "TestBot"
        assert result["entity_type"] == "ai"

    def test_update_existing_entity(self, tmp_db, sample_provider):
        eid = tmp_db.add_entity("Old", "ai", "#fff", sample_provider, "m", 0.5, 512, "")
        result = save_entity(
            tmp_db, "New", "ai", entity_id=eid,
            provider_id=sample_provider, model="m",
        )
        assert result["name"] == "New"


class TestDeleteEntity:
    def test_delete_entity(self, tmp_db, sample_ai_entity):
        result = delete_entity(tmp_db, sample_ai_entity)
        assert isinstance(result, dict)


class TestGetEntities:
    def test_get_entities(self, tmp_db, sample_ai_entity):
        entities = get_entities(tmp_db)
        assert len(entities) >= 1

    def test_get_inactive_entities(self, tmp_db, sample_ai_entity):
        tmp_db.delete_entity(sample_ai_entity)
        inactive = get_inactive_entities(tmp_db)
        assert isinstance(inactive, list)


class TestReactivateEntity:
    def test_reactivate(self, tmp_db, sample_ai_entity):
        tmp_db.delete_entity(sample_ai_entity)
        result = reactivate_entity(tmp_db, sample_ai_entity)
        assert isinstance(result, bool)
