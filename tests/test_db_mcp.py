"""Tests for MCP server and expert definition CRUD."""

import sqlite3

import pytest

from consensus.database import Database


class TestMCPServerCRUD:
    def test_add_mcp_server(self, tmp_db):
        server_id = tmp_db.add_mcp_server(
            name="BM Librarian",
            description="Biomedical literature search",
            command="uvx",
            args=["bmlibrarian-mcp"],
            env={"BRAVE_API_KEY": "test"},
        )
        assert server_id is not None
        server = tmp_db.get_mcp_server(server_id)
        assert server["name"] == "BM Librarian"
        assert server["description"] == "Biomedical literature search"
        assert server["enabled"] == 1
        assert server["args"] == ["bmlibrarian-mcp"]
        assert server["env"] == {"BRAVE_API_KEY": "test"}

    def test_get_mcp_servers(self, tmp_db):
        tmp_db.add_mcp_server("Server1", "Desc1", "cmd1")
        tmp_db.add_mcp_server("Server2", "Desc2", "cmd2")
        servers = tmp_db.get_mcp_servers()
        assert len(servers) == 2

    def test_get_mcp_servers_enabled_only(self, tmp_db):
        s1 = tmp_db.add_mcp_server("Enabled", "Desc", "cmd")
        s2 = tmp_db.add_mcp_server("Disabled", "Desc", "cmd")
        tmp_db.update_mcp_server(s2, enabled=0)
        servers = tmp_db.get_mcp_servers(enabled_only=True)
        assert len(servers) == 1
        assert servers[0]["name"] == "Enabled"

    def test_update_mcp_server(self, tmp_db):
        sid = tmp_db.add_mcp_server("Old", "Desc", "cmd")
        tmp_db.update_mcp_server(sid, name="New", enabled=0)
        server = tmp_db.get_mcp_server(sid)
        assert server["name"] == "New"
        assert server["enabled"] == 0

    def test_update_mcp_server_args_and_env(self, tmp_db):
        sid = tmp_db.add_mcp_server("S", "D", "cmd", args=["a"], env={"K": "V"})
        tmp_db.update_mcp_server(sid, args=["b", "c"], env={"X": "Y"})
        server = tmp_db.get_mcp_server(sid)
        assert server["args"] == ["b", "c"]
        assert server["env"] == {"X": "Y"}

    def test_delete_mcp_server(self, tmp_db):
        sid = tmp_db.add_mcp_server("ToDelete", "Desc", "cmd")
        tmp_db.delete_mcp_server(sid)
        assert tmp_db.get_mcp_server(sid) is None

    def test_delete_mcp_server_cascades_expert_definitions(self, tmp_db, sample_ai_entity):
        sid = tmp_db.add_mcp_server("Server", "Desc", "cmd")
        tmp_db.add_expert_definition(sample_ai_entity, sid, "tool1")
        tmp_db.delete_mcp_server(sid)
        assert tmp_db.get_expert_definition(sample_ai_entity) is None

    def test_get_nonexistent_mcp_server(self, tmp_db):
        assert tmp_db.get_mcp_server(99999) is None

    def test_duplicate_name_rejected(self, tmp_db):
        tmp_db.add_mcp_server("Unique", "Desc", "cmd")
        with pytest.raises(sqlite3.IntegrityError):
            tmp_db.add_mcp_server("Unique", "Desc2", "cmd2")


class TestExpertDefinitions:
    def test_add_expert_definition(self, tmp_db, sample_ai_entity):
        sid = tmp_db.add_mcp_server("Server", "Desc", "cmd")
        eid = tmp_db.add_expert_definition(
            entity_id=sample_ai_entity,
            mcp_server_id=sid,
            tool_name="fact_check_claim",
            description="Medical fact-checker",
            default_arguments={"search_provider": "both"},
            timeout_seconds=300,
        )
        assert eid is not None
        defn = tmp_db.get_expert_definition(sample_ai_entity)
        assert defn["tool_name"] == "fact_check_claim"
        assert defn["timeout_seconds"] == 300
        assert defn["default_arguments"] == {"search_provider": "both"}

    def test_get_expert_definition_includes_server_info(self, tmp_db, sample_ai_entity):
        sid = tmp_db.add_mcp_server(
            "BM Librarian", "Biomedical search", "uvx",
            args=["bmlibrarian-mcp"], env={"KEY": "val"},
        )
        tmp_db.add_expert_definition(sample_ai_entity, sid, "search", "Searcher")
        defn = tmp_db.get_expert_definition(sample_ai_entity)
        assert defn["server_name"] == "BM Librarian"
        assert defn["command"] == "uvx"
        assert defn["server_args"] == ["bmlibrarian-mcp"]
        assert defn["server_env"] == {"KEY": "val"}
        assert defn["server_enabled"] == 1

    def test_get_expert_definitions(self, tmp_db, sample_ai_entity):
        sid = tmp_db.add_mcp_server("Server", "Desc", "cmd")
        tmp_db.add_expert_definition(sample_ai_entity, sid, "tool1", "Expert 1")
        experts = tmp_db.get_expert_definitions()
        assert len(experts) >= 1
        assert "server_name" in experts[0]
        assert "command" in experts[0]

    def test_delete_expert_definition(self, tmp_db, sample_ai_entity):
        sid = tmp_db.add_mcp_server("Server", "Desc", "cmd")
        tmp_db.add_expert_definition(sample_ai_entity, sid, "tool1")
        tmp_db.delete_expert_definition(sample_ai_entity)
        assert tmp_db.get_expert_definition(sample_ai_entity) is None

    def test_get_nonexistent_expert_definition(self, tmp_db):
        assert tmp_db.get_expert_definition(99999) is None

    def test_unique_entity_constraint(self, tmp_db, sample_ai_entity):
        sid = tmp_db.add_mcp_server("Server", "Desc", "cmd")
        tmp_db.add_expert_definition(sample_ai_entity, sid, "tool1")
        with pytest.raises(sqlite3.IntegrityError):
            tmp_db.add_expert_definition(sample_ai_entity, sid, "tool2")
