"""Tests for consensus.app_discussion_flow — active discussion operations."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from consensus.app_discussion_flow import (
    is_pass,
    submit_human_message,
    submit_moderator_message,
)
from consensus.models import Discussion, Entity, EntityType, MessageRole


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
