"""Tests for consensus.app_discussion_setup — discussion setup and membership."""

from consensus.app_discussion_setup import (
    add_to_discussion,
    remove_from_discussion,
    set_moderator,
    set_topic,
)
from consensus.models import Discussion


class TestAddToDiscussion:
    def test_add_entity(self, tmp_db, sample_ai_entity):
        disc = Discussion()
        result = add_to_discussion(disc, tmp_db, sample_ai_entity)
        assert "error" not in result
        assert len(disc.entities) == 1

    def test_duplicate_entity_returns_error(self, tmp_db, sample_ai_entity):
        disc = Discussion()
        add_to_discussion(disc, tmp_db, sample_ai_entity)
        result = add_to_discussion(disc, tmp_db, sample_ai_entity)
        assert "error" in result

    def test_nonexistent_entity_returns_error(self, tmp_db):
        disc = Discussion()
        result = add_to_discussion(disc, tmp_db, 9999)
        assert "error" in result

    def test_add_as_moderator(self, tmp_db, sample_ai_entity):
        disc = Discussion()
        add_to_discussion(disc, tmp_db, sample_ai_entity, is_moderator=True)
        assert disc.moderator_id == sample_ai_entity


class TestRemoveFromDiscussion:
    def test_remove_entity(self, tmp_db, sample_ai_entity, sample_human_entity):
        disc = Discussion()
        add_to_discussion(disc, tmp_db, sample_ai_entity, is_moderator=True)
        add_to_discussion(disc, tmp_db, sample_human_entity)
        result = remove_from_discussion(disc, tmp_db, sample_human_entity)
        assert result is True
        assert len(disc.entities) == 1


class TestSetTopic:
    def test_set_topic(self):
        disc = Discussion()
        assert set_topic(disc, "AI regulation") is True
        assert disc.topic == "AI regulation"


class TestSetModerator:
    def test_set_moderator(self, tmp_db, sample_ai_entity):
        disc = Discussion()
        add_to_discussion(disc, tmp_db, sample_ai_entity)
        assert set_moderator(disc, sample_ai_entity) is True
        assert disc.moderator_id == sample_ai_entity

