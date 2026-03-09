"""Tests for consensus.app_discussion_setup — discussion setup and membership."""

import pytest

from consensus.app_discussion_setup import (
    add_to_discussion,
    auto_assign_da_tools,
    remove_from_discussion,
    reorder_da_in_turn_order,
    set_moderator,
    set_participant_role,
    set_topic,
    start_discussion,
)
from consensus.models import Discussion, Entity, EntityType, MessageRole
from consensus.moderator import Moderator


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


class TestReorderDaTurnOrder:
    def test_da_moved_to_end(self):
        disc = Discussion(turn_order=[1, 2, 3], current_turn_index=0)
        disc.member_roles = {1: "standard", 2: "devils_advocate", 3: "standard"}
        reorder_da_in_turn_order(disc)
        assert disc.turn_order[-1] == 2

    def test_no_da_is_noop(self):
        disc = Discussion(turn_order=[1, 2, 3], current_turn_index=0)
        disc.member_roles = {1: "standard", 2: "standard", 3: "standard"}
        reorder_da_in_turn_order(disc)
        assert disc.turn_order == [1, 2, 3]
