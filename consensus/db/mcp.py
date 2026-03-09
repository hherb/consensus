"""Database mixin for MCP server and expert definition CRUD."""

import json
import sqlite3
import time
from typing import Optional


class MCPMixin:
    """Mixin providing MCP server and expert definition database operations.

    Expects host class to provide:
        conn: sqlite3.Connection
        _lock: threading.Lock
        _execute_write(sql, params) -> sqlite3.Cursor
    """

    # ------------------------------------------------------------------
    # MCP Servers
    # ------------------------------------------------------------------

    def add_mcp_server(self, name: str, description: str = "",
                       command: str = "", args: list | None = None,
                       env: dict | None = None,
                       enabled: bool = True) -> int:
        """Register an MCP server. Returns the new server ID."""
        now = time.time()
        cur = self._execute_write(
            "INSERT INTO mcp_servers "
            "(name, description, command, args, env, enabled, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (name, description, command,
             json.dumps(args or []), json.dumps(env or {}),
             int(enabled), now, now),
        )
        return cur.lastrowid

    def get_mcp_server(self, server_id: int) -> Optional[dict]:
        """Retrieve a single MCP server by ID."""
        row = self.conn.execute(
            "SELECT * FROM mcp_servers WHERE id=?", (server_id,)
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["args"] = json.loads(d["args"])
        d["env"] = json.loads(d["env"])
        return d

    def get_mcp_servers(self, enabled_only: bool = False) -> list[dict]:
        """Return all MCP servers, optionally filtered to enabled only."""
        sql = "SELECT * FROM mcp_servers"
        if enabled_only:
            sql += " WHERE enabled=1"
        sql += " ORDER BY name"
        results = []
        for row in self.conn.execute(sql).fetchall():
            d = dict(row)
            d["args"] = json.loads(d["args"])
            d["env"] = json.loads(d["env"])
            results.append(d)
        return results

    def update_mcp_server(self, server_id: int, **kwargs: object) -> None:
        """Update an MCP server's mutable fields."""
        allowed = {"name", "description", "command", "args", "env", "enabled"}
        sets: list[str] = []
        vals: list[object] = []
        for k, v in kwargs.items():
            if k in allowed:
                if k == "args":
                    v = json.dumps(v)
                elif k == "env":
                    v = json.dumps(v)
                sets.append(f"{k}=?")
                vals.append(v)
        if sets:
            sets.append("updated_at=?")
            vals.append(time.time())
            vals.append(server_id)
            self._execute_write(
                f"UPDATE mcp_servers SET {','.join(sets)} WHERE id=?",
                tuple(vals),
            )

    def delete_mcp_server(self, server_id: int) -> None:
        """Delete an MCP server and its associated expert definitions atomically."""
        with self._lock:
            self.conn.execute(
                "DELETE FROM expert_definitions WHERE mcp_server_id=?",
                (server_id,),
            )
            self.conn.execute(
                "DELETE FROM mcp_servers WHERE id=?", (server_id,),
            )
            self.conn.commit()

    # ------------------------------------------------------------------
    # Expert Definitions
    # ------------------------------------------------------------------

    def add_expert_definition(self, entity_id: int, mcp_server_id: int,
                              tool_name: str, description: str = "",
                              default_arguments: dict | None = None,
                              query_param_name: str = "query",
                              timeout_seconds: int = 300) -> int:
        """Link an entity to an MCP server tool as an expert. Returns the definition ID."""
        cur = self._execute_write(
            "INSERT INTO expert_definitions "
            "(entity_id, mcp_server_id, tool_name, description, "
            "default_arguments, query_param_name, timeout_seconds) VALUES (?,?,?,?,?,?,?)",
            (entity_id, mcp_server_id, tool_name, description,
             json.dumps(default_arguments or {}), query_param_name, timeout_seconds),
        )
        return cur.lastrowid

    def _parse_expert_row(self, row: sqlite3.Row) -> dict:
        """Convert an expert_definitions JOIN row to a dict with parsed JSON."""
        d = dict(row)
        d["default_arguments"] = json.loads(d["default_arguments"])
        if "server_args" in d:
            d["server_args"] = json.loads(d["server_args"])
        if "server_env" in d:
            d["server_env"] = json.loads(d["server_env"])
        return d

    _EXPERT_JOIN_SQL = (
        "SELECT ed.*, ms.name AS server_name, ms.command, "
        "ms.args AS server_args, ms.env AS server_env, "
        "ms.enabled AS server_enabled "
        "FROM expert_definitions ed "
        "JOIN mcp_servers ms ON ed.mcp_server_id = ms.id"
    )

    def get_expert_definition(self, entity_id: int) -> Optional[dict]:
        """Retrieve the expert definition for an entity, including server info."""
        row = self.conn.execute(
            self._EXPERT_JOIN_SQL + " WHERE ed.entity_id=?",
            (entity_id,),
        ).fetchone()
        if not row:
            return None
        return self._parse_expert_row(row)

    def get_expert_definitions(self) -> list[dict]:
        """Return all expert definitions with server info."""
        return [
            self._parse_expert_row(row)
            for row in self.conn.execute(self._EXPERT_JOIN_SQL).fetchall()
        ]

    def delete_expert_definition(self, entity_id: int) -> None:
        """Delete the expert definition for an entity."""
        self._execute_write(
            "DELETE FROM expert_definitions WHERE entity_id=?",
            (entity_id,),
        )
