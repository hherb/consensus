"""Tests for message and storyboard CRUD operations."""

import pytest

from consensus.database import Database


class TestMessages:
    def test_add_and_get_messages(self, tmp_db, sample_ai_entity):
        did = tmp_db.create_discussion("T", sample_ai_entity)
        tmp_db.add_message(did, sample_ai_entity, "Hello", "moderator", turn_number=0)
        tmp_db.add_message(did, sample_ai_entity, "World", "participant", turn_number=1)
        msgs = tmp_db.get_messages(did)
        assert len(msgs) == 2
        assert msgs[0]["content"] == "Hello"
        assert msgs[1]["content"] == "World"

    def test_add_message_with_ai_metadata(self, tmp_db, sample_ai_entity):
        did = tmp_db.create_discussion("T", sample_ai_entity)
        tmp_db.add_message(
            did, sample_ai_entity, "AI says hi", "participant",
            turn_number=1, model_used="gpt-4",
            prompt_tokens=10, completion_tokens=20,
            total_tokens=30, latency_ms=500,
        )
        msgs = tmp_db.get_messages(did)
        assert msgs[0]["model_used"] == "gpt-4"
        assert msgs[0]["total_tokens"] == 30

    def test_get_max_turn_number(self, tmp_db, sample_ai_entity):
        did = tmp_db.create_discussion("T", sample_ai_entity)
        tmp_db.add_message(did, sample_ai_entity, "A", "participant", turn_number=1)
        tmp_db.add_message(did, sample_ai_entity, "B", "participant", turn_number=5)
        tmp_db.add_message(did, sample_ai_entity, "C", "participant", turn_number=3)
        assert tmp_db.get_max_turn_number(did) == 5

    def test_get_max_turn_number_empty(self, tmp_db, sample_ai_entity):
        did = tmp_db.create_discussion("T", sample_ai_entity)
        assert tmp_db.get_max_turn_number(did) == 0

    def test_add_message_with_cost(self, tmp_db, sample_ai_entity):
        did = tmp_db.create_discussion("T", sample_ai_entity)
        mid = tmp_db.add_message(
            did, sample_ai_entity, "Costly response", "participant",
            turn_number=1, cost=0.0042,
        )
        msgs = tmp_db.get_messages(did)
        assert len(msgs) == 1
        assert msgs[0]["cost"] == pytest.approx(0.0042)

    def test_add_message_cost_defaults_to_none(self, tmp_db, sample_ai_entity):
        did = tmp_db.create_discussion("T", sample_ai_entity)
        tmp_db.add_message(did, sample_ai_entity, "No cost", "participant", turn_number=1)
        msgs = tmp_db.get_messages(did)
        assert msgs[0]["cost"] is None


class TestStoryboard:
    def test_add_and_get_storyboard(self, tmp_db, sample_ai_entity):
        did = tmp_db.create_discussion("T", sample_ai_entity)
        tmp_db.add_storyboard_entry(did, 1, "Great point", sample_ai_entity)
        tmp_db.add_storyboard_entry(did, 2, "Counter-argument", sample_ai_entity)
        entries = tmp_db.get_storyboard(did)
        assert len(entries) == 2
        assert entries[0]["summary"] == "Great point"
