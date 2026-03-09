"""Tests for tool provider, entity-tool, and discussion override CRUD."""

import pytest

from consensus.database import Database


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
