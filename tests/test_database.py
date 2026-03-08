"""Tests for consensus.database — CRUD operations, integrity, edge cases."""

import sqlite3
import time

import pytest

from consensus.database import Database

try:
    import sqlite_vec  # noqa: F401
    HAS_SQLITE_VEC = True
except ImportError:
    HAS_SQLITE_VEC = False
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

    def test_migrations_tracked(self, tmp_db):
        row = tmp_db.conn.execute(
            "SELECT MAX(version) FROM migrations"
        ).fetchone()
        assert row[0] >= 1

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

    def test_get_entities_filter_by_type(self, tmp_db, sample_ai_entity, sample_human_entity):
        ai_entities = tmp_db.get_entities(entity_type="ai")
        assert all(e["entity_type"] == "ai" for e in ai_entities)
        human_entities = tmp_db.get_entities(entity_type="human")
        assert all(e["entity_type"] == "human" for e in human_entities)

    def test_get_entities_include_inactive(self, tmp_db, sample_ai_entity):
        # Create a reference so delete soft-deletes instead of hard-deletes
        did = tmp_db.create_discussion("T", sample_ai_entity)
        tmp_db.add_discussion_member(did, sample_ai_entity, True, False)
        tmp_db.delete_entity(sample_ai_entity)
        entities = tmp_db.get_entities(include_inactive=True)
        ids = [e["id"] for e in entities]
        assert sample_ai_entity in ids

    def test_get_inactive_entities(self, tmp_db, sample_ai_entity):
        # Force soft-delete by creating a reference first
        did = tmp_db.create_discussion("T", sample_ai_entity)
        tmp_db.add_discussion_member(did, sample_ai_entity, True, False)
        tmp_db.delete_entity(sample_ai_entity)
        inactive = tmp_db.get_inactive_entities()
        ids = [e["id"] for e in inactive]
        assert sample_ai_entity in ids


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

    def test_soft_delete_discussion(self, tmp_db, sample_ai_entity):
        did = tmp_db.create_discussion("ToDelete", sample_ai_entity)
        count = tmp_db.soft_delete_discussions([did])
        assert count == 1
        d = tmp_db.get_discussion(did)
        assert d["deleted_at"] is not None
        # Should not appear in get_discussions list
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
        # Soft-delete, then backdate deleted_at
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


# --- Discussion Members ---

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

    def test_get_prompts_filter_by_role(self, tmp_db):
        prompts = tmp_db.get_prompts(role="moderator")
        assert all(p["role"] == "moderator" for p in prompts)
        assert len(prompts) > 0

    def test_get_prompts_filter_by_target(self, tmp_db):
        prompts = tmp_db.get_prompts(target="ai")
        assert all(p["target"] == "ai" for p in prompts)

    def test_get_prompts_filter_by_role_and_task(self, tmp_db):
        prompts = tmp_db.get_prompts(role="moderator", task="system")
        assert all(p["role"] == "moderator" and p["task"] == "system" for p in prompts)


# --- Tool Providers ---

class TestToolProviders:
    def test_add_and_get(self, tmp_db):
        pid = tmp_db.add_tool_provider("web_search", "python")
        providers = tmp_db.get_tool_providers()
        names = [p["name"] for p in providers]
        assert "web_search" in names

    def test_add_duplicate_ignored(self, tmp_db):
        pid1 = tmp_db.add_tool_provider("web_search", "python")
        pid2 = tmp_db.add_tool_provider("web_search", "python")
        assert pid1 == pid2

    def test_delete_tool_provider(self, tmp_db):
        pid = tmp_db.add_tool_provider("temp", "python")
        tmp_db.delete_tool_provider(pid)
        providers = tmp_db.get_tool_providers()
        ids = [p["id"] for p in providers]
        assert pid not in ids


# --- Entity Tools ---

class TestEntityTools:
    def test_assign_and_get(self, tmp_db, sample_ai_entity):
        tmp_db.add_entity_tool(sample_ai_entity, "web_search", "private")
        tools = tmp_db.get_entity_tools(sample_ai_entity)
        assert len(tools) == 1
        assert tools[0]["tool_name"] == "web_search"
        assert tools[0]["access_mode"] == "private"

    def test_get_specific_tool(self, tmp_db, sample_ai_entity):
        tmp_db.add_entity_tool(sample_ai_entity, "web_search", "shared")
        tool = tmp_db.get_entity_tool(sample_ai_entity, "web_search")
        assert tool is not None
        assert tool["access_mode"] == "shared"

    def test_get_nonexistent_tool(self, tmp_db, sample_ai_entity):
        assert tmp_db.get_entity_tool(sample_ai_entity, "nope") is None

    def test_remove_entity_tool(self, tmp_db, sample_ai_entity):
        tmp_db.add_entity_tool(sample_ai_entity, "web_search")
        tmp_db.remove_entity_tool(sample_ai_entity, "web_search")
        assert tmp_db.get_entity_tools(sample_ai_entity) == []

    def test_shared_tools_for_discussion(self, tmp_db, sample_ai_entity):
        did = tmp_db.create_discussion("T", sample_ai_entity)
        tmp_db.add_discussion_member(did, sample_ai_entity, True, False)
        tmp_db.add_entity_tool(sample_ai_entity, "shared_tool", "shared")
        tmp_db.add_entity_tool(sample_ai_entity, "private_tool", "private")
        shared = tmp_db.get_shared_tools_for_discussion(did)
        names = [t["tool_name"] for t in shared]
        assert "shared_tool" in names
        assert "private_tool" not in names


# --- Discussion Tool Overrides ---

class TestDiscussionToolOverrides:
    def test_set_and_get_override(self, tmp_db, sample_ai_entity):
        did = tmp_db.create_discussion("T", sample_ai_entity)
        tmp_db.set_discussion_tool_override(did, sample_ai_entity, "web_search", False)
        overrides = tmp_db.get_discussion_tool_overrides(did, sample_ai_entity)
        assert len(overrides) == 1
        assert overrides[0]["tool_name"] == "web_search"
        assert overrides[0]["enabled"] == 0

    def test_override_upsert(self, tmp_db, sample_ai_entity):
        did = tmp_db.create_discussion("T", sample_ai_entity)
        tmp_db.set_discussion_tool_override(did, sample_ai_entity, "web_search", False)
        tmp_db.set_discussion_tool_override(did, sample_ai_entity, "web_search", True)
        overrides = tmp_db.get_discussion_tool_overrides(did, sample_ai_entity)
        assert len(overrides) == 1
        assert overrides[0]["enabled"] == 1


# --- _update_row safety ---

class TestUpdateRow:
    def test_rejects_invalid_table(self, tmp_db):
        with pytest.raises(ValueError, match="Invalid table"):
            tmp_db._update_row("users; DROP TABLE providers;--", 1, {"name"})

    def test_update_row_filters_unknown_fields(self, tmp_db, sample_provider):
        # unknown_field should be silently ignored
        tmp_db._update_row("providers", sample_provider,
                           allowed={"name"}, name="X", unknown_field="Y")
        p = tmp_db.get_provider(sample_provider)
        assert p["name"] == "X"


# --- Memory and Knowledge Graph ---

@pytest.mark.skipif(not HAS_SQLITE_VEC, reason="sqlite_vec not installed")
class TestMemoryAndKG:
    def test_memory_config_get_set(self, tmp_db):
        config = tmp_db.get_memory_config()
        assert "embedding_backend" in config
        tmp_db.set_memory_config("test_key", "test_val")
        config = tmp_db.get_memory_config()
        assert config["test_key"] == "test_val"

    def test_add_entity_memory(self, tmp_db, sample_ai_entity):
        tmp_db.add_entity_memory("mem1", sample_ai_entity, "Remember this")
        tmp_db.set_entity_memory_embedding("mem1", b"\x00" * 16)
        memories = tmp_db.get_entity_memories_with_embeddings(sample_ai_entity)
        assert len(memories) == 1
        assert memories[0]["content"] == "Remember this"

    def test_delete_entity_memory(self, tmp_db, sample_ai_entity):
        tmp_db.add_entity_memory("mem2", sample_ai_entity, "Forget this")
        result = tmp_db.delete_entity_memory("mem2", sample_ai_entity)
        assert result is True

    def test_delete_nonexistent_memory(self, tmp_db, sample_ai_entity):
        result = tmp_db.delete_entity_memory("nope", sample_ai_entity)
        assert result is False

    def test_message_embedding(self, tmp_db, sample_ai_entity):
        did = tmp_db.create_discussion("T", sample_ai_entity)
        mid = tmp_db.add_message(did, sample_ai_entity, "test msg", "participant", 1)
        unindexed = tmp_db.get_unindexed_message_ids(did)
        assert str(mid) in unindexed
        tmp_db.set_message_embedding(str(mid), b"\x00" * 16)
        unindexed_after = tmp_db.get_unindexed_message_ids(did)
        assert str(mid) not in unindexed_after

    def test_get_message_content(self, tmp_db, sample_ai_entity):
        did = tmp_db.create_discussion("T", sample_ai_entity)
        mid = tmp_db.add_message(did, sample_ai_entity, "hello", "participant", 1)
        assert tmp_db.get_message_content(str(mid)) == "hello"
        assert tmp_db.get_message_content("99999") is None

    def test_get_messages_with_embeddings(self, tmp_db, sample_ai_entity):
        did = tmp_db.create_discussion("T", sample_ai_entity)
        mid = tmp_db.add_message(did, sample_ai_entity, "test", "participant", 1)
        tmp_db.set_message_embedding(str(mid), b"\x00" * 16)
        results = tmp_db.get_messages_with_embeddings()
        assert len(results) >= 1
        assert results[0]["content"] == "test"

    def test_get_messages_with_embeddings_topic_filter(self, tmp_db, sample_ai_entity):
        did = tmp_db.create_discussion("Unique Topic XYZ", sample_ai_entity)
        mid = tmp_db.add_message(did, sample_ai_entity, "test", "participant", 1)
        tmp_db.set_message_embedding(str(mid), b"\x00" * 16)
        results = tmp_db.get_messages_with_embeddings(topic_filter="Unique Topic")
        assert len(results) >= 1
        results_empty = tmp_db.get_messages_with_embeddings(topic_filter="NoMatch")
        assert len(results_empty) == 0

    def test_kg_node_upsert_and_get(self, tmp_db):
        tmp_db.upsert_kg_node("n1", "free will", "concept", "The ability to choose")
        node = tmp_db.get_kg_node_by_label("free will")
        assert node is not None
        assert node["description"] == "The ability to choose"

    def test_kg_node_not_found(self, tmp_db):
        assert tmp_db.get_kg_node_by_label("nonexistent") is None

    def test_kg_edge_and_neighbors(self, tmp_db):
        tmp_db.upsert_kg_node("n1", "A", "concept")
        tmp_db.upsert_kg_node("n2", "B", "concept")
        tmp_db.add_kg_edge("e1", "n1", "n2", "supports")
        neighbors = tmp_db.get_kg_neighbors("n1")
        assert len(neighbors) == 1
        assert neighbors[0]["label"] == "B"
        assert neighbors[0]["relation"] == "supports"
        assert neighbors[0]["direction"] == "out"
        # Reverse direction
        neighbors_rev = tmp_db.get_kg_neighbors("n2")
        assert len(neighbors_rev) == 1
        assert neighbors_rev[0]["direction"] == "in"

    def test_kg_nodes_with_embeddings(self, tmp_db):
        tmp_db.upsert_kg_node("n1", "test_node", "concept")
        tmp_db.set_kg_node_embedding("n1", b"\x00" * 16)
        nodes = tmp_db.get_kg_nodes_with_embeddings()
        assert len(nodes) >= 1
        assert nodes[0]["label"] == "test_node"


# --- Migrator ---

import threading
import consensus.migrator
from consensus.migrator import run_migrations


class TestMigrator:
    def _fresh_conn(self, tmp_path, name="mig.db"):
        db_path = str(tmp_path / name)
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        return conn, db_path

    def test_creates_migrations_table(self, tmp_path):
        conn, db_path = self._fresh_conn(tmp_path)
        lock = threading.Lock()
        consensus.migrator._migrations_done.discard(db_path)
        run_migrations(conn, lock, db_path)
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()]
        assert "migrations" in tables
        conn.close()

    def test_applies_sql_migrations(self, tmp_path):
        conn, db_path = self._fresh_conn(tmp_path)
        lock = threading.Lock()
        consensus.migrator._migrations_done.discard(db_path)
        run_migrations(conn, lock, db_path)
        row = conn.execute("SELECT MAX(version) FROM migrations").fetchone()
        assert row[0] >= 1
        conn.close()

    def test_creates_schema_tables(self, tmp_path):
        """Baseline migration creates all expected tables."""
        conn, db_path = self._fresh_conn(tmp_path)
        lock = threading.Lock()
        consensus.migrator._migrations_done.discard(db_path)
        run_migrations(conn, lock, db_path)
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()]
        for expected in ["providers", "entities", "prompts", "discussions",
                         "discussion_members", "messages", "storyboard_entries",
                         "tool_providers", "entity_tools",
                         "discussion_tool_overrides"]:
            assert expected in tables, f"Missing table: {expected}"
        conn.close()

    def test_idempotent_rerun(self, tmp_path):
        conn, db_path = self._fresh_conn(tmp_path)
        lock = threading.Lock()
        consensus.migrator._migrations_done.discard(db_path)
        run_migrations(conn, lock, db_path)
        consensus.migrator._migrations_done.discard(db_path)
        run_migrations(conn, lock, db_path)
        rows = conn.execute("SELECT * FROM migrations").fetchall()
        versions = [r[0] for r in rows]
        assert len(versions) == len(set(versions))  # no duplicates
        conn.close()

    def test_single_execution_guard(self, tmp_path):
        conn, db_path = self._fresh_conn(tmp_path)
        lock = threading.Lock()
        consensus.migrator._migrations_done.discard(db_path)
        run_migrations(conn, lock, db_path)
        assert db_path in consensus.migrator._migrations_done
        # Second call is a no-op
        run_migrations(conn, lock, db_path)
        conn.close()

    def test_legacy_schema_version_stamped(self, tmp_path):
        """Existing DB with schema_version table gets baseline stamped."""
        conn, db_path = self._fresh_conn(tmp_path)
        lock = threading.Lock()
        # Simulate a legacy database
        conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
        conn.execute("INSERT INTO schema_version (version) VALUES (1)")
        # Also create the tables that would exist in a legacy DB
        conn.execute("CREATE TABLE providers (id INTEGER PRIMARY KEY, name TEXT NOT NULL, base_url TEXT NOT NULL, api_key_env TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL)")
        conn.commit()
        consensus.migrator._migrations_done.discard(db_path)
        run_migrations(conn, lock, db_path)
        # schema_version should be gone
        sv = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_version'"
        ).fetchone()
        assert sv is None
        # baseline should be stamped
        row = conn.execute(
            "SELECT version FROM migrations WHERE version=1"
        ).fetchone()
        assert row is not None
        conn.close()
