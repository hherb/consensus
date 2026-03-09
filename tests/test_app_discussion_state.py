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
from consensus.models import Discussion, MessageRole


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
