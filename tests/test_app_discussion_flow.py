"""Tests for consensus.app_discussion_flow — active discussion operations."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from consensus.app_discussion_flow import (
    calculate_discussion_cost,
    generate_ai_turn,
    is_pass,
    submit_human_message,
    submit_moderator_message,
)
from consensus.models import Discussion, Entity, EntityType, Message, MessageRole
from consensus.pricing import PricingCache


class TestIsPass:
    """Tests for the is_pass helper function."""

    def test_pass_bracket(self):
        """Bracket notation [PASS] is recognised."""
        assert is_pass("[PASS]") is True

    def test_pass_plain(self):
        """Plain PASS is recognised."""
        assert is_pass("PASS") is True

    def test_pass_formatted(self):
        """Formatted '*Name passed this round.*' is recognised."""
        assert is_pass("*Alice passed this round.*") is True

    def test_not_pass(self):
        """Regular discussion content is not a pass."""
        assert is_pass("I think we should consider...") is False

    def test_pass_with_whitespace(self):
        """Leading/trailing whitespace does not prevent detection."""
        assert is_pass("  [PASS]  ") is True

    def test_pass_with_markdown(self):
        """Bold markdown around [PASS] is recognised."""
        assert is_pass("**[PASS]**") is True

    def test_phrase_mid_sentence_is_not_pass(self):
        """The pass phrase inside a longer contribution is not a pass."""
        assert is_pass(
            "I disagree that everyone passed this round. Here is my view: ..."
        ) is False


class TestSubmitHumanMessage:
    """Tests for submit_human_message."""

    def test_submit_message(self, tmp_db, discussion_with_entities):
        """A human whose turn it is can submit a message."""
        disc = discussion_with_entities
        did = tmp_db.create_discussion(disc.topic, disc.moderator_id)
        disc.id = did
        # Current speaker is at index 0 (AI entity) — switch to human (index 1)
        disc.current_turn_index = 1
        speaker = disc.current_speaker
        result = submit_human_message(disc, tmp_db, speaker.id, "Hello world")
        assert "error" not in result
        assert result["content"] == "Hello world"

    def test_wrong_turn_returns_error(self, tmp_db, discussion_with_entities):
        """Submitting when it is not the entity's turn returns an error."""
        disc = discussion_with_entities
        did = tmp_db.create_discussion(disc.topic, disc.moderator_id)
        disc.id = did
        # Current speaker is at index 0 — pick the other entity
        other = [e for e in disc.entities if e.id != disc.current_speaker.id][0]
        result = submit_human_message(disc, tmp_db, other.id, "Hello")
        assert "error" in result

    def test_entity_not_found(self, tmp_db, discussion_with_entities):
        """Submitting for a non-existent entity returns an error."""
        disc = discussion_with_entities
        result = submit_human_message(disc, tmp_db, 9999, "Hello")
        assert "error" in result


class TestSubmitModeratorMessage:
    """Tests for submit_moderator_message."""

    def test_submit_moderator_message(self, tmp_db, discussion_with_entities):
        """The moderator can submit a message."""
        disc = discussion_with_entities
        did = tmp_db.create_discussion(disc.topic, disc.moderator_id)
        disc.id = did
        result = submit_moderator_message(disc, tmp_db, "Moderator says hello")
        assert "error" not in result

    def test_no_moderator_returns_error(self, tmp_db):
        """Submitting a moderator message with no moderator returns an error."""
        disc = Discussion()
        result = submit_moderator_message(disc, tmp_db, "Hello")
        assert "error" in result


class TestCalculateDiscussionCost:
    """Tests for the calculate_discussion_cost helper."""

    def test_empty_messages(self):
        """Discussion with no messages returns 0."""
        disc = Discussion()
        assert calculate_discussion_cost(disc) == 0.0

    def test_sums_costs(self):
        """Sums cost from all messages."""
        disc = Discussion(messages=[
            Message(entity_id=1, entity_name="A", content="x",
                    role=MessageRole.PARTICIPANT, cost=0.05),
            Message(entity_id=2, entity_name="B", content="y",
                    role=MessageRole.PARTICIPANT, cost=0.10),
        ])
        assert abs(calculate_discussion_cost(disc) - 0.15) < 1e-9

    def test_none_costs_treated_as_zero(self):
        """Messages with cost=None (human messages) are treated as $0."""
        disc = Discussion(messages=[
            Message(entity_id=1, entity_name="A", content="x",
                    role=MessageRole.PARTICIPANT, cost=None),
            Message(entity_id=2, entity_name="B", content="y",
                    role=MessageRole.PARTICIPANT, cost=0.10),
        ])
        assert abs(calculate_discussion_cost(disc) - 0.10) < 1e-9


class TestCostLimitEnforcement:
    """Tests for cost limit checks in generate_ai_turn."""

    @pytest.mark.asyncio
    async def test_preflight_blocks_when_over_limit(
        self, tmp_db, discussion_with_entities
    ):
        """generate_ai_turn returns cost_limit_reached when budget exceeded."""
        disc = discussion_with_entities
        disc.cost_limit = 0.50
        # Add messages that exceed the limit
        disc.messages = [
            Message(entity_id=1, entity_name="A", content="x",
                    role=MessageRole.PARTICIPANT, cost=0.30),
            Message(entity_id=2, entity_name="B", content="y",
                    role=MessageRole.PARTICIPANT, cost=0.25),
        ]
        pricing = PricingCache(tmp_db.conn, tmp_db._lock)
        moderator = MagicMock()
        result = await generate_ai_turn(disc, moderator, tmp_db, pricing)
        assert result["cost_limit_reached"] is True
        assert result["total_cost"] >= 0.50

    @pytest.mark.asyncio
    async def test_preflight_allows_when_under_limit(
        self, tmp_db, discussion_with_entities
    ):
        """generate_ai_turn proceeds normally when under budget."""
        disc = discussion_with_entities
        disc.cost_limit = 10.0
        disc.messages = [
            Message(entity_id=1, entity_name="A", content="x",
                    role=MessageRole.PARTICIPANT, cost=0.01),
        ]
        pricing = PricingCache(tmp_db.conn, tmp_db._lock)
        moderator = MagicMock()
        # Mock generate_turn to return a response
        mock_resp = MagicMock()
        mock_resp.content = "AI response"
        mock_resp.model = "test-model"
        mock_resp.prompt_tokens = 10
        mock_resp.completion_tokens = 20
        mock_resp.total_tokens = 30
        mock_resp.latency_ms = 100
        mock_resp.tool_calls = []
        mock_resp.warning = None
        moderator.generate_turn = AsyncMock(return_value=mock_resp)
        moderator.prompt_id = MagicMock(return_value=None)

        did = tmp_db.create_discussion(disc.topic, disc.moderator_id)
        disc.id = did

        result = await generate_ai_turn(disc, moderator, tmp_db, pricing)
        assert "cost_limit_reached" not in result
        assert "error" not in result

    @pytest.mark.asyncio
    async def test_no_limit_when_zero(
        self, tmp_db, discussion_with_entities
    ):
        """cost_limit=0 means unlimited — no blocking even with high costs."""
        disc = discussion_with_entities
        disc.cost_limit = 0.0
        disc.messages = [
            Message(entity_id=1, entity_name="A", content="x",
                    role=MessageRole.PARTICIPANT, cost=999.99),
        ]
        pricing = PricingCache(tmp_db.conn, tmp_db._lock)
        moderator = MagicMock()
        mock_resp = MagicMock()
        mock_resp.content = "AI response"
        mock_resp.model = "test-model"
        mock_resp.prompt_tokens = 10
        mock_resp.completion_tokens = 20
        mock_resp.total_tokens = 30
        mock_resp.latency_ms = 100
        mock_resp.tool_calls = []
        mock_resp.warning = None
        moderator.generate_turn = AsyncMock(return_value=mock_resp)
        moderator.prompt_id = MagicMock(return_value=None)

        did = tmp_db.create_discussion(disc.topic, disc.moderator_id)
        disc.id = did

        result = await generate_ai_turn(disc, moderator, tmp_db, pricing)
        assert "cost_limit_reached" not in result
