"""Tests for MCP config file loading."""

import json
import os
import tempfile

import pytest

from consensus.mcp_config import load_mcp_config, merge_config_servers


class TestLoadMcpConfig:
    """Test loading MCP server definitions from config files."""

    def test_load_json_config(self, tmp_path):
        """Load a JSON config file with MCP server definitions."""
        config = {
            "mcp_servers": [
                {
                    "name": "biomedical",
                    "description": "BioMedical literature search",
                    "command": "uvx",
                    "args": ["bmlibrarian"],
                    "env": {"BM_API_KEY": "test-key"},
                    "enabled": True,
                },
                {
                    "name": "filesystem",
                    "description": "File system access",
                    "command": "npx",
                    "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
                },
            ],
        }
        config_path = tmp_path / "mcp_servers.json"
        config_path.write_text(json.dumps(config))

        servers = load_mcp_config(str(config_path))
        assert len(servers) == 2
        assert servers[0]["name"] == "biomedical"
        assert servers[0]["args"] == ["bmlibrarian"]
        assert servers[0]["env"] == {"BM_API_KEY": "test-key"}
        assert servers[0]["enabled"] is True
        assert servers[1]["name"] == "filesystem"
        assert servers[1].get("enabled", True) is True

    def test_load_toml_config(self, tmp_path):
        """Load a TOML config file with MCP server definitions."""
        toml_content = '''
[[mcp_servers]]
name = "biomedical"
description = "BioMedical literature search"
command = "uvx"
args = ["bmlibrarian"]

[mcp_servers.env]
BM_API_KEY = "test-key"

[[mcp_servers]]
name = "filesystem"
description = "File system access"
command = "npx"
args = ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
'''
        config_path = tmp_path / "mcp_servers.toml"
        config_path.write_text(toml_content)

        servers = load_mcp_config(str(config_path))
        assert len(servers) == 2
        assert servers[0]["name"] == "biomedical"

    def test_load_nonexistent_file_returns_empty(self):
        """Return empty list for non-existent config file."""
        servers = load_mcp_config("/nonexistent/path.json")
        assert servers == []

    def test_load_invalid_json_returns_empty(self, tmp_path):
        """Return empty list for invalid JSON."""
        config_path = tmp_path / "bad.json"
        config_path.write_text("{invalid json")

        servers = load_mcp_config(str(config_path))
        assert servers == []

    def test_load_missing_required_fields_skips_entry(self, tmp_path):
        """Skip entries missing required 'name' or 'command' fields."""
        config = {
            "mcp_servers": [
                {"name": "valid", "command": "echo"},
                {"description": "no name or command"},
                {"name": "no-command"},
            ],
        }
        config_path = tmp_path / "partial.json"
        config_path.write_text(json.dumps(config))

        servers = load_mcp_config(str(config_path))
        assert len(servers) == 1
        assert servers[0]["name"] == "valid"

    def test_defaults_applied(self, tmp_path):
        """Missing optional fields get sensible defaults."""
        config = {
            "mcp_servers": [
                {"name": "minimal", "command": "echo"},
            ],
        }
        config_path = tmp_path / "minimal.json"
        config_path.write_text(json.dumps(config))

        servers = load_mcp_config(str(config_path))
        assert servers[0]["description"] == ""
        assert servers[0]["args"] == []
        assert servers[0]["env"] == {}
        assert servers[0]["enabled"] is True

    def test_http_transport_detected(self, tmp_path):
        """Entries with 'url' field are detected as HTTP transport."""
        config = {
            "mcp_servers": [
                {
                    "name": "remote-server",
                    "description": "Remote MCP server",
                    "url": "https://mcp.example.com/sse",
                },
            ],
        }
        config_path = tmp_path / "http.json"
        config_path.write_text(json.dumps(config))

        servers = load_mcp_config(str(config_path))
        assert len(servers) == 1
        assert servers[0]["transport"] == "http"
        assert servers[0]["url"] == "https://mcp.example.com/sse"
        assert servers[0].get("command", "") == ""


class TestMergeConfigServers:
    """Test merging config file servers with existing DB servers."""

    def test_new_servers_added(self):
        """Config servers not in DB are returned for insertion."""
        config_servers = [
            {"name": "new-server", "command": "echo", "description": "",
             "args": [], "env": {}, "enabled": True, "transport": "stdio"},
        ]
        db_servers = []

        to_add, to_update = merge_config_servers(config_servers, db_servers)
        assert len(to_add) == 1
        assert to_add[0]["name"] == "new-server"
        assert len(to_update) == 0

    def test_existing_server_not_duplicated(self):
        """Config servers already in DB (by name) are not re-added."""
        config_servers = [
            {"name": "existing", "command": "echo", "description": "",
             "args": [], "env": {}, "enabled": True, "transport": "stdio"},
        ]
        db_servers = [
            {"id": 1, "name": "existing", "command": "echo",
             "description": "", "args": [], "env": {}, "enabled": 1},
        ]

        to_add, to_update = merge_config_servers(config_servers, db_servers)
        assert len(to_add) == 0

    def test_existing_server_updated_when_changed(self):
        """Config servers with changed fields trigger an update."""
        config_servers = [
            {"name": "server1", "command": "new-cmd", "description": "updated",
             "args": ["--new"], "env": {}, "enabled": True, "transport": "stdio"},
        ]
        db_servers = [
            {"id": 1, "name": "server1", "command": "old-cmd",
             "description": "old", "args": [], "env": {}, "enabled": 1},
        ]

        to_add, to_update = merge_config_servers(config_servers, db_servers)
        assert len(to_add) == 0
        assert len(to_update) == 1
        assert to_update[0]["id"] == 1
        assert to_update[0]["command"] == "new-cmd"
