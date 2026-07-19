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
    @pytest.mark.asyncio
    async def test_basic_structure(self, mod_setup):
        moderator, disc, ai, human = mod_setup
        # Create a persisted discussion so DB-driven context loading works
        did = moderator.db.create_discussion("AI ethics", ai.id)
        disc.id = did
        moderator.db.add_discussion_member(did, ai.id, is_moderator=True, turn_position=0)
        moderator.db.add_discussion_member(did, human.id, turn_position=1)
        msgs = await moderator._build_context("You are a moderator.", "Summarize.",
                                               current_entity_id=ai.id)
        assert msgs[0]["role"] == "system"
        assert msgs[0]["content"] == "You are a moderator."
        assert msgs[1]["role"] == "user"
        assert "AI ethics" in msgs[1]["content"]
        assert msgs[-1]["role"] == "user"
        assert msgs[-1]["content"] == "Summarize."

    @pytest.mark.asyncio
    async def test_message_roles_assigned_correctly(self, mod_setup):
        moderator, disc, ai, human = mod_setup
        # Persist messages to DB so DB-driven context loading picks them up
        did = moderator.db.create_discussion("AI ethics", ai.id)
        disc.id = did
        moderator.db.add_discussion_member(did, ai.id, is_moderator=True, also_participant=True, turn_position=0)
        moderator.db.add_discussion_member(did, human.id, turn_position=1)
        moderator.db.add_message(did, ai.id, "I think...", "participant", turn_number=1)
        moderator.db.add_message(did, human.id, "I disagree.", "participant", turn_number=2)
        msgs = await moderator._build_context("sys", "task", current_entity_id=ai.id)
        # ai's message -> "assistant", human's -> "user" with prefix
        ai_msg = msgs[2]  # after system + topic
        human_msg = msgs[3]
        assert ai_msg["role"] == "assistant"
        assert ai_msg["content"] == "I think..."
        assert human_msg["role"] == "user"
        assert f"[{human.name}]" in human_msg["content"]

    @pytest.mark.asyncio
    async def test_context_limited_to_recent_messages(self, mod_setup):
        moderator, disc, ai, human = mod_setup
        # Create a persisted discussion so DB-driven context loading works
        did = moderator.db.create_discussion("AI ethics", ai.id)
        disc.id = did
        moderator.db.add_discussion_member(did, ai.id, is_moderator=True, turn_position=0)
        moderator.db.add_discussion_member(did, human.id, turn_position=1)
        # Add more messages than the default window
        for i in range(CONTEXT_MESSAGE_LIMIT + 10):
            moderator.db.add_message(
                did, human.id, f"msg-{i}", "participant", turn_number=i,
            )
        msgs = await moderator._build_context("sys", "task")
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


class TestToolLoopProgress:
    """The tool execution loop must report each tool call through the
    progress callback so the UI can show what a participant is doing
    during long tool-using turns."""

    def _tool_result(self, message: dict) -> dict:
        return {
            "message": message,
            "finish_reason": "stop",
            "model": "m",
            "prompt_tokens": 1,
            "completion_tokens": 1,
            "total_tokens": 2,
            "latency_ms": 5,
        }

    @pytest.mark.asyncio
    async def test_progress_callback_fires_per_tool_call(
        self, tmp_db, mod_setup,
    ):
        from unittest.mock import AsyncMock, MagicMock

        from consensus.tools import ToolDefinition, ToolResult

        moderator, disc, ai, human = mod_setup
        disc.id = tmp_db.create_discussion(disc.topic, disc.moderator_id)

        registry = MagicMock()
        registry.get_tools_for_entity = AsyncMock(return_value=[
            ToolDefinition(name="web_search", description="", parameters={}),
        ])
        registry.execute = AsyncMock(
            return_value=ToolResult(content="results"))
        moderator._tool_registry = registry

        events: list[dict] = []
        moderator._progress_callback = events.append

        client = MagicMock()
        client.complete_with_tools = AsyncMock(side_effect=[
            self._tool_result({
                "content": "",
                "tool_calls": [{
                    "id": "tc1",
                    "function": {"name": "web_search",
                                 "arguments": '{"query": "x"}'},
                }],
            }),
            self._tool_result({"content": "final answer"}),
        ])
        moderator._get_client = MagicMock(return_value=client)

        resp = await moderator.generate_turn(ai)

        assert resp.content == "final answer"
        tool_events = [e for e in events if e.get("tool_name") == "web_search"]
        assert tool_events, f"no web_search progress event in {events}"
        assert tool_events[0]["entity_name"] == ai.name
        assert tool_events[0]["message"]

    @pytest.mark.asyncio
    async def test_callback_error_does_not_break_turn(
        self, tmp_db, mod_setup,
    ):
        from unittest.mock import AsyncMock, MagicMock

        from consensus.tools import ToolDefinition, ToolResult

        moderator, disc, ai, human = mod_setup
        disc.id = tmp_db.create_discussion(disc.topic, disc.moderator_id)

        registry = MagicMock()
        registry.get_tools_for_entity = AsyncMock(return_value=[
            ToolDefinition(name="web_search", description="", parameters={}),
        ])
        registry.execute = AsyncMock(
            return_value=ToolResult(content="results"))
        moderator._tool_registry = registry

        def broken_callback(data):
            raise RuntimeError("UI push failed")

        moderator._progress_callback = broken_callback

        client = MagicMock()
        client.complete_with_tools = AsyncMock(side_effect=[
            self._tool_result({
                "content": "",
                "tool_calls": [{
                    "id": "tc1",
                    "function": {"name": "web_search",
                                 "arguments": '{"query": "x"}'},
                }],
            }),
            self._tool_result({"content": "final answer"}),
        ])
        moderator._get_client = MagicMock(return_value=client)

        resp = await moderator.generate_turn(ai)
        assert resp.content == "final answer"
