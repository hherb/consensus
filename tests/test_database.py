"""Tests for consensus.database — CRUD operations, integrity, edge cases."""

import sqlite3
import time

import pytest

from consensus.database import Database
from consensus.models import DEFAULT_TEMPERATURE, DEFAULT_MAX_TOKENS


# --- Schema & initialization ---

class TestDatabaseInit:
    def test_tables_created(self, tmp_db):
        tables = [r[0] for r in tmp_db.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()]
        for t in ["providers", "entities", "prompts", "discussions",
                   "messages", "storyboard_entries", "discussion_members"]:
            assert t in tables, f"Missing table: {t}"

    def test_schema_version_set(self, tmp_db):
        row = tmp_db.conn.execute("SELECT version FROM schema_version").fetchone()
        assert row is not None
        assert row[0] == 1

    def test_default_prompts_seeded(self, tmp_db):
        prompts = tmp_db.get_prompts()
        assert len(prompts) > 0
        roles = {p["role"] for p in prompts}
        assert "moderator" in roles
        assert "participant" in roles

    def test_foreign_keys_enabled(self, tmp_db):
        row = tmp_db.conn.execute("PRAGMA foreign_keys").fetchone()
        assert row[0] == 1

    def test_idempotent_init(self, tmp_path):
        """Creating a second Database on the same path should not duplicate data."""
        db_path = str(tmp_path / "test.db")
        db1 = Database(db_path)
        prompt_count_1 = len(db1.get_prompts())
        db1.conn.close()

        db2 = Database(db_path)
        prompt_count_2 = len(db2.get_prompts())
        db2.conn.close()
        assert prompt_count_1 == prompt_count_2


# --- Providers ---

class TestProviders:
    def test_add_and_get(self, tmp_db):
        pid = tmp_db.add_provider("OpenAI", "https://api.openai.com/v1", "OPENAI_KEY")
        p = tmp_db.get_provider(pid)
        assert p["name"] == "OpenAI"
        assert p["base_url"] == "https://api.openai.com/v1"
        assert p["api_key_env"] == "OPENAI_KEY"

    def test_get_providers_returns_all(self, tmp_db):
        tmp_db.add_provider("A", "http://a", "")
        tmp_db.add_provider("B", "http://b", "")
        providers = tmp_db.get_providers()
        names = {p["name"] for p in providers}
        assert "A" in names and "B" in names

    def test_update_provider(self, tmp_db, sample_provider):
        tmp_db.update_provider(sample_provider, name="Updated", base_url="http://new")
        p = tmp_db.get_provider(sample_provider)
        assert p["name"] == "Updated"
        assert p["base_url"] == "http://new"

    def test_delete_provider(self, tmp_db, sample_provider):
        tmp_db.delete_provider(sample_provider)
        assert tmp_db.get_provider(sample_provider) is None

    def test_get_nonexistent_provider(self, tmp_db):
        assert tmp_db.get_provider(99999) is None


# --- Entities ---

class TestEntities:
    def test_add_and_get_ai_entity(self, tmp_db, sample_provider):
        eid = tmp_db.add_entity(
            "TestBot", "ai", "#ff0000", sample_provider,
            "gpt-4", 0.5, 512, "system prompt",
        )
        e = tmp_db.get_entity(eid)
        assert e["name"] == "TestBot"
        assert e["entity_type"] == "ai"
        assert e["model"] == "gpt-4"
        assert e["temperature"] == 0.5

    def test_add_and_get_human_entity(self, tmp_db):
        eid = tmp_db.add_entity("Human", "human", "#00ff00")
        e = tmp_db.get_entity(eid)
        assert e["name"] == "Human"
        assert e["entity_type"] == "human"

    def test_update_entity(self, tmp_db, sample_ai_entity):
        tmp_db.update_entity(sample_ai_entity, name="NewName", temperature=0.9)
        e = tmp_db.get_entity(sample_ai_entity)
        assert e["name"] == "NewName"
        assert e["temperature"] == 0.9

    def test_delete_entity_soft_delete(self, tmp_db, sample_ai_entity):
        result = tmp_db.delete_entity(sample_ai_entity)
        # Should be soft-deleted (deactivated), not hard-deleted
        # Active entities list should not include it
        entities = tmp_db.get_entities()
        ids = [e["id"] for e in entities]
        assert sample_ai_entity not in ids

    def test_reactivate_entity(self, tmp_db, sample_ai_entity):
        # Create a discussion referencing the entity so delete soft-deletes
        did = tmp_db.create_discussion("T", sample_ai_entity)
        tmp_db.add_discussion_member(did, sample_ai_entity, True, False)
        result = tmp_db.delete_entity(sample_ai_entity)
        assert result == {"deactivated": True}
        result = tmp_db.reactivate_entity(sample_ai_entity)
        assert result is True
        entities = tmp_db.get_entities()
        ids = [e["id"] for e in entities]
        assert sample_ai_entity in ids

    def test_get_entities_returns_active_only(self, tmp_db, sample_ai_entity, sample_human_entity):
        tmp_db.delete_entity(sample_ai_entity)
        entities = tmp_db.get_entities()
        ids = [e["id"] for e in entities]
        assert sample_ai_entity not in ids
        assert sample_human_entity in ids

    def test_get_nonexistent_entity(self, tmp_db):
        assert tmp_db.get_entity(99999) is None

    def test_invalid_entity_type_rejected(self, tmp_db):
        with pytest.raises(sqlite3.IntegrityError):
            tmp_db.add_entity("Bad", "robot", "#000")


# --- Discussions ---

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


# --- Messages ---

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


# --- Storyboard ---

class TestStoryboard:
    def test_add_and_get_storyboard(self, tmp_db, sample_ai_entity):
        did = tmp_db.create_discussion("T", sample_ai_entity)
        tmp_db.add_storyboard_entry(did, 1, "Great point", sample_ai_entity)
        tmp_db.add_storyboard_entry(did, 2, "Counter-argument", sample_ai_entity)
        entries = tmp_db.get_storyboard(did)
        assert len(entries) == 2
        assert entries[0]["summary"] == "Great point"


# --- Prompts ---

class TestPrompts:
    def test_save_and_get_prompt(self, tmp_db):
        pid = tmp_db.save_prompt(
            None, "Custom Prompt", "moderator", "ai", "custom_task", "Do {thing}",
        )
        p = tmp_db.get_prompt(pid)
        assert p["name"] == "Custom Prompt"
        assert p["content"] == "Do {thing}"

    def test_update_prompt(self, tmp_db):
        pid = tmp_db.save_prompt(None, "P1", "moderator", "ai", "task1", "Content1")
        tmp_db.save_prompt(pid, "P1-Updated", "moderator", "ai", "task1", "Content2")
        p = tmp_db.get_prompt(pid)
        assert p["name"] == "P1-Updated"
        assert p["content"] == "Content2"

    def test_delete_prompt(self, tmp_db):
        pid = tmp_db.save_prompt(None, "ToDelete", "participant", "ai", "t", "c")
        tmp_db.delete_prompt(pid)
        assert tmp_db.get_prompt(pid) is None

    def test_get_prompt_by_task(self, tmp_db):
        tmp_db.save_prompt(None, "P", "participant", "human", "custom_unique_task", "Help text")
        row = tmp_db.get_prompt_by_task("participant", "human", "custom_unique_task")
        assert row is not None
        assert row["content"] == "Help text"

    def test_get_prompt_by_task_not_found(self, tmp_db):
        row = tmp_db.get_prompt_by_task("participant", "human", "nonexistent_task_xyz")
        assert row is None


# --- _update_row safety ---

class TestUpdateRow:
    def test_rejects_invalid_table(self, tmp_db):
        with pytest.raises(ValueError, match="Invalid table"):
            tmp_db._update_row("users; DROP TABLE providers;--", 1, {"name"})
