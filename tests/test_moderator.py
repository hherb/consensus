"""Tests for consensus.moderator — turn management, context building, prompt resolution."""

import pytest

from consensus.models import (
    AIConfig, Discussion, Entity, EntityType, Message, MessageRole,
)
from consensus.moderator import Moderator, CONTEXT_MESSAGE_LIMIT
from consensus.database import Database


@pytest.fixture
def mod_setup(tmp_db, sample_ai_entity, sample_human_entity):
    """Set up a Moderator with a live discussion."""
    ai_row = tmp_db.get_entity(sample_ai_entity)
    human_row = tmp_db.get_entity(sample_human_entity)
    ai = Entity.from_db_row(ai_row)
    human = Entity.from_db_row(human_row)

    disc = Discussion(
        topic="AI ethics",
        entities=[ai, human],
        moderator_id=ai.id,
        turn_order=[ai.id, human.id],
        current_turn_index=0,
        turn_number=1,
        is_active=True,
        status="active",
    )
    moderator = Moderator(disc, tmp_db)
    return moderator, disc, ai, human


class TestAdvanceTurn:
    def test_advances_to_next_speaker(self, mod_setup):
        moderator, disc, ai, human = mod_setup
        next_speaker = moderator.advance_turn()
        assert next_speaker.id == human.id
        assert disc.current_turn_index == 1
        assert disc.turn_number == 2

    def test_wraps_around(self, mod_setup):
        moderator, disc, ai, human = mod_setup
        moderator.advance_turn()  # -> human
        next_speaker = moderator.advance_turn()  # -> ai
        assert next_speaker.id == ai.id
        assert disc.current_turn_index == 0
        assert disc.turn_number == 3

    def test_returns_none_when_no_turn_order(self, tmp_db):
        disc = Discussion(topic="T", turn_order=[])
        mod = Moderator(disc, tmp_db)
        assert mod.advance_turn() is None

    def test_multiple_cycles(self, mod_setup):
        moderator, disc, ai, human = mod_setup
        for i in range(10):
            moderator.advance_turn()
        assert disc.turn_number == 11
        # After 10 advances from index 0: 10 % 2 = 0
        assert disc.current_turn_index == 0


class TestReassignTurn:
    def test_reassign_to_valid_entity(self, mod_setup):
        moderator, disc, ai, human = mod_setup
        result = moderator.reassign_turn(human.id)
        assert result is not None
        assert result.id == human.id
        assert disc.current_turn_index == 1

    def test_reassign_to_invalid_entity(self, mod_setup):
        moderator, disc, ai, human = mod_setup
        result = moderator.reassign_turn(99999)
        assert result is None

    def test_reassign_to_entity_not_in_turn_order(self, mod_setup):
        moderator, disc, ai, human = mod_setup
        # Add a 3rd entity to discussion but not to turn_order
        extra = Entity(name="Eve", entity_type=EntityType.HUMAN, id=999)
        disc.entities.append(extra)
        result = moderator.reassign_turn(999)
        assert result is None


class TestBuildContext:
    def test_basic_structure(self, mod_setup):
        moderator, disc, ai, human = mod_setup
        msgs = moderator._build_context("You are a moderator.", "Summarize.",
                                        current_entity_id=ai.id)
        assert msgs[0]["role"] == "system"
        assert msgs[0]["content"] == "You are a moderator."
        assert msgs[1]["role"] == "user"
        assert "AI ethics" in msgs[1]["content"]
        assert msgs[-1]["role"] == "user"
        assert msgs[-1]["content"] == "Summarize."

    def test_message_roles_assigned_correctly(self, mod_setup):
        moderator, disc, ai, human = mod_setup
        disc.messages.append(Message(
            entity_id=ai.id, entity_name=ai.name,
            content="I think...", role=MessageRole.PARTICIPANT,
        ))
        disc.messages.append(Message(
            entity_id=human.id, entity_name=human.name,
            content="I disagree.", role=MessageRole.PARTICIPANT,
        ))
        msgs = moderator._build_context("sys", "task", current_entity_id=ai.id)
        # ai's message -> "assistant", human's -> "user" with prefix
        ai_msg = msgs[2]  # after system + topic
        human_msg = msgs[3]
        assert ai_msg["role"] == "assistant"
        assert ai_msg["content"] == "I think..."
        assert human_msg["role"] == "user"
        assert f"[{human.name}]" in human_msg["content"]

    def test_context_limited_to_recent_messages(self, mod_setup):
        moderator, disc, ai, human = mod_setup
        # Add more messages than the limit
        for i in range(CONTEXT_MESSAGE_LIMIT + 10):
            disc.messages.append(Message(
                entity_id=human.id, entity_name=human.name,
                content=f"msg-{i}", role=MessageRole.PARTICIPANT,
            ))
        msgs = moderator._build_context("sys", "task")
        # system + topic + CONTEXT_MESSAGE_LIMIT messages + task
        assert len(msgs) == 2 + CONTEXT_MESSAGE_LIMIT + 1


class TestResolvePrompt:
    def test_resolves_with_variables(self, mod_setup):
        moderator, disc, ai, human = mod_setup
        # Use an existing default prompt
        result = moderator.resolve_prompt(
            "moderator", "ai", "system",
            entity_name="TestMod", topic="Testing", participants="A, B",
        )
        # Should have replaced template variables
        assert "{entity_name}" not in result
        assert "{topic}" not in result

    def test_returns_empty_for_nonexistent_prompt(self, mod_setup):
        moderator, disc, ai, human = mod_setup
        result = moderator.resolve_prompt(
            "moderator", "ai", "nonexistent_xyz_task",
        )
        assert result == ""


class TestParticipantNames:
    def test_format(self, mod_setup):
        moderator, disc, ai, human = mod_setup
        names = moderator._participant_names()
        assert ai.name in names
        assert human.name in names
        assert "AI" in names
        assert "Human" in names
