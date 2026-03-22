"""Tests for consensus.app_discussion_state — discussion state management."""

import pytest

from consensus.app_discussion_state import (
    delete_discussions,
    get_export_data,
    load_discussion,
    pause_discussion,
    reopen_discussion,
    reset_discussion,
    restore_discussion,
    resume_discussion,
)
from consensus.models import Discussion, Message, MessageRole


class TestPauseDiscussion:
    def test_pause_active_discussion(self, tmp_db, discussion_with_entities):
        disc = discussion_with_entities
        did = tmp_db.create_discussion(disc.topic, disc.moderator_id)
        disc.id = did
        result = pause_discussion(disc, tmp_db)
        assert "error" not in result
        assert disc.status == "paused"
        assert disc.is_active is False

    def test_pause_inactive_returns_error(self, tmp_db):
        disc = Discussion()
        result = pause_discussion(disc, tmp_db)
        assert "error" in result


class TestResumeDiscussion:
    def test_resume_paused_discussion(self, tmp_db, discussion_with_entities):
        disc = discussion_with_entities
        did = tmp_db.create_discussion(disc.topic, disc.moderator_id)
        disc.id = did
        pause_discussion(disc, tmp_db)
        result = resume_discussion(disc, tmp_db)
        assert "error" not in result
        assert disc.status == "active"
        assert disc.is_active is True

    def test_resume_increases_budgets_when_rounds_exhausted(
        self, tmp_db, discussion_with_entities
    ):
        """Resuming a discussion paused at the round limit must increase
        max_rounds so the next turn doesn't immediately terminate."""
        disc = discussion_with_entities
        did = tmp_db.create_discussion(disc.topic, disc.moderator_id)
        disc.id = did
        disc.max_rounds = 3
        # With 2 entities in turn_order, round = (turn_number-1)//2 + 1.
        # turn_number=7 → round 4, exceeding max_rounds=3.
        disc.turn_number = 7
        tmp_db.update_discussion(did, max_rounds=3)
        pause_discussion(disc, tmp_db)

        result = resume_discussion(disc, tmp_db)
        assert "error" not in result
        assert disc.max_rounds > 3
        assert disc.current_round <= disc.max_rounds

    def test_resume_increases_budgets_when_cost_exhausted(
        self, tmp_db, discussion_with_entities
    ):
        """Resuming a discussion paused at the cost limit must increase
        cost_limit so the next turn doesn't immediately terminate."""
        disc = discussion_with_entities
        did = tmp_db.create_discussion(disc.topic, disc.moderator_id)
        disc.id = did
        disc.cost_limit = 1.0
        tmp_db.update_discussion(did, cost_limit=1.0)
        # Add a message with cost >= limit
        tmp_db.add_message(did, disc.moderator_id, "test", "participant",
                           turn_number=1, cost=1.5)
        disc.messages.append(Message(
            entity_id=disc.moderator_id, entity_name="System",
            content="test", role=MessageRole.PARTICIPANT, cost=1.5,
        ))
        pause_discussion(disc, tmp_db)

        result = resume_discussion(disc, tmp_db)
        assert "error" not in result
        assert disc.cost_limit > 1.0

    def test_reopen_then_resume_no_double_increase(
        self, tmp_db, discussion_with_entities
    ):
        """reopen → resume should only increase budgets once, not twice."""
        disc = discussion_with_entities
        did = tmp_db.create_discussion(disc.topic, disc.moderator_id)
        disc.id = did
        disc.max_rounds = 3
        disc.turn_number = 7  # round 4, past max_rounds=3
        disc.status = "concluded"
        tmp_db.update_discussion(did, max_rounds=3, status="concluded")

        reopen_discussion(disc, tmp_db)
        assert disc.status == "paused"
        # reopen increases the budget once
        assert disc.max_rounds == 6  # original 3 * 2

        resume_discussion(disc, tmp_db)
        # resume should NOT increase again since rounds are no longer exhausted
        assert disc.max_rounds == 6
        assert disc.method_state.get("_continuation_count") == 2

    def test_resume_active_returns_error(self, tmp_db, discussion_with_entities):
        disc = discussion_with_entities
        did = tmp_db.create_discussion(disc.topic, disc.moderator_id)
        disc.id = did
        result = resume_discussion(disc, tmp_db)
        assert "error" in result


class TestGetExportData:
    def test_nonexistent_discussion(self, tmp_db):
        result = get_export_data(tmp_db, 9999)
        assert "error" in result


class TestResetDiscussion:
    def test_reset_returns_fresh_state(self, tmp_db):
        disc, mod = reset_discussion(tmp_db, lambda pid, env: "", None)
        assert disc.topic == ""
        assert disc.entities == []


class TestDeleteDiscussions:
    def test_delete_discussions(self, tmp_db):
        did = tmp_db.create_discussion("test", None)
        result = delete_discussions(tmp_db, [did])
        assert result["deleted"] >= 0


class TestRestoreDiscussion:
    def test_restore_discussion(self, tmp_db):
        did = tmp_db.create_discussion("test", None)
        tmp_db.soft_delete_discussions([did])
        result = restore_discussion(tmp_db, did)
        assert "restored" in result
