"""Tests for discussion and discussion member CRUD operations."""

import time

import pytest

from consensus.database import Database


class TestDiscussions:
    def test_create_and_get(self, tmp_db, sample_ai_entity):
        did = tmp_db.create_discussion("Test Topic", sample_ai_entity)
        d = tmp_db.get_discussion(did)
        assert d["topic"] == "Test Topic"
        assert d["moderator_id"] == sample_ai_entity
        assert d["status"] == "setup"

    def test_update_discussion_status(self, tmp_db, sample_ai_entity):
        did = tmp_db.create_discussion("T", sample_ai_entity)
        tmp_db.update_discussion(did, status="active", started_at=time.time())
        d = tmp_db.get_discussion(did)
        assert d["status"] == "active"
        assert d["started_at"] is not None

    def test_get_discussions_list(self, tmp_db, sample_ai_entity):
        tmp_db.create_discussion("A", sample_ai_entity)
        tmp_db.create_discussion("B", sample_ai_entity)
        discussions = tmp_db.get_discussions()
        assert len(discussions) >= 2

    def test_discussion_members(self, tmp_db, sample_ai_entity, sample_human_entity):
        did = tmp_db.create_discussion("T", sample_ai_entity)
        tmp_db.add_discussion_member(did, sample_ai_entity, is_moderator=True,
                                     also_participant=False, turn_position=None)
        tmp_db.add_discussion_member(did, sample_human_entity, is_moderator=False,
                                     also_participant=True, turn_position=0)
        members = tmp_db.get_discussion_members(did)
        assert len(members) == 2
        member_ids = {m["entity_id"] for m in members}
        assert sample_ai_entity in member_ids
        assert sample_human_entity in member_ids

    def test_remove_discussion_member(self, tmp_db, sample_ai_entity, sample_human_entity):
        did = tmp_db.create_discussion("T", sample_ai_entity)
        tmp_db.add_discussion_member(did, sample_ai_entity, True, False)
        tmp_db.add_discussion_member(did, sample_human_entity, False, True, 0)
        tmp_db.remove_discussion_member(did, sample_human_entity)
        members = tmp_db.get_discussion_members(did)
        assert len(members) == 1

    def test_soft_delete_discussion(self, tmp_db, sample_ai_entity):
        did = tmp_db.create_discussion("ToDelete", sample_ai_entity)
        count = tmp_db.soft_delete_discussions([did])
        assert count == 1
        d = tmp_db.get_discussion(did)
        assert d["deleted_at"] is not None
        discussions = tmp_db.get_discussions()
        ids = [d["id"] for d in discussions]
        assert did not in ids

    def test_soft_delete_multiple(self, tmp_db, sample_ai_entity):
        d1 = tmp_db.create_discussion("A", sample_ai_entity)
        d2 = tmp_db.create_discussion("B", sample_ai_entity)
        count = tmp_db.soft_delete_discussions([d1, d2])
        assert count == 2

    def test_soft_delete_empty_list(self, tmp_db):
        assert tmp_db.soft_delete_discussions([]) == 0

    def test_soft_delete_idempotent(self, tmp_db, sample_ai_entity):
        did = tmp_db.create_discussion("T", sample_ai_entity)
        tmp_db.soft_delete_discussions([did])
        count = tmp_db.soft_delete_discussions([did])
        assert count == 0  # already deleted

    def test_restore_discussion(self, tmp_db, sample_ai_entity):
        did = tmp_db.create_discussion("T", sample_ai_entity)
        tmp_db.soft_delete_discussions([did])
        result = tmp_db.restore_discussion(did)
        assert result is True
        discussions = tmp_db.get_discussions()
        ids = [d["id"] for d in discussions]
        assert did in ids

    def test_restore_non_deleted_discussion(self, tmp_db, sample_ai_entity):
        did = tmp_db.create_discussion("T", sample_ai_entity)
        result = tmp_db.restore_discussion(did)
        assert result is False

    def test_purge_deleted_discussions(self, tmp_db, sample_ai_entity, sample_human_entity):
        did = tmp_db.create_discussion("T", sample_ai_entity)
        tmp_db.add_discussion_member(did, sample_ai_entity, True, False)
        tmp_db.add_message(did, sample_ai_entity, "msg", "moderator", 1)
        tmp_db.add_storyboard_entry(did, 1, "sum", sample_ai_entity)
        tmp_db.soft_delete_discussions([did])
        tmp_db.conn.execute(
            "UPDATE discussions SET deleted_at = ? WHERE id = ?",
            (time.time() - 86400 * 30, did),
        )
        tmp_db.conn.commit()
        count = tmp_db.purge_deleted_discussions(max_days=7)
        assert count == 1
        assert tmp_db.get_discussion(did) is None
        assert tmp_db.get_messages(did) == []
        assert tmp_db.get_storyboard(did) == []
        assert tmp_db.get_discussion_members(did) == []


class TestDiscussionMembers:
    def test_add_member_with_role(self, tmp_db, sample_ai_entity):
        did = tmp_db.create_discussion("T", sample_ai_entity)
        tmp_db.add_discussion_member(did, sample_ai_entity, True, False,
                                     participant_role="devils_advocate")
        member = tmp_db.get_discussion_member(did, sample_ai_entity)
        assert member is not None
        assert member["participant_role"] == "devils_advocate"

    def test_update_member_role(self, tmp_db, sample_ai_entity):
        did = tmp_db.create_discussion("T", sample_ai_entity)
        tmp_db.add_discussion_member(did, sample_ai_entity, False, True,
                                     participant_role="standard")
        tmp_db.update_member_role(did, sample_ai_entity, "devils_advocate")
        member = tmp_db.get_discussion_member(did, sample_ai_entity)
        assert member["participant_role"] == "devils_advocate"

    def test_turn_position_ordering(self, tmp_db, sample_ai_entity, sample_human_entity):
        did = tmp_db.create_discussion("T", sample_ai_entity)
        tmp_db.add_discussion_member(did, sample_human_entity, False, True, turn_position=1)
        tmp_db.add_discussion_member(did, sample_ai_entity, True, False, turn_position=0)
        members = tmp_db.get_discussion_members(did)
        assert members[0]["entity_id"] == sample_ai_entity
        assert members[1]["entity_id"] == sample_human_entity

    def test_get_nonexistent_member(self, tmp_db, sample_ai_entity):
        did = tmp_db.create_discussion("T", sample_ai_entity)
        assert tmp_db.get_discussion_member(did, 99999) is None
