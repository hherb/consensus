"""Database mixin for tool provider, entity-tool, and discussion override CRUD."""

import time
from typing import Optional


class ToolsMixin:
    """Mixin providing tool-related database operations.

    Expects host class to provide:
        conn: sqlite3.Connection
        _lock: threading.Lock
        _execute_write(sql, params) -> sqlite3.Cursor
    """

    # ------------------------------------------------------------------
    # Tool Providers
    # ------------------------------------------------------------------

    def add_tool_provider(self, name: str, provider_type: str,
                          config_json: str = "{}") -> int:
        """Register a tool provider. Returns the provider ID."""
        cur = self._execute_write(
            "INSERT OR IGNORE INTO tool_providers "
            "(name, type, config_json, created_at) VALUES (?,?,?,?)",
            (name, provider_type, config_json, time.time()),
        )
        if cur.lastrowid:
            return cur.lastrowid
        row = self.conn.execute(
            "SELECT id FROM tool_providers WHERE name=?", (name,)
        ).fetchone()
        return row[0] if row else 0

    def get_tool_providers(self) -> list[dict]:
        """Return all registered tool providers."""
        return [dict(r) for r in
                self.conn.execute(
                    "SELECT * FROM tool_providers ORDER BY name"
                ).fetchall()]

    def delete_tool_provider(self, provider_id: int) -> None:
        """Delete a tool provider."""
        self._execute_write(
            "DELETE FROM tool_providers WHERE id=?", (provider_id,))

    # ------------------------------------------------------------------
    # Entity-Tool Assignments
    # ------------------------------------------------------------------

    def add_entity_tool(self, entity_id: int, tool_name: str,
                        access_mode: str = "private") -> None:
        """Assign a tool to an entity."""
        self._execute_write(
            "INSERT OR REPLACE INTO entity_tools "
            "(entity_id, tool_name, access_mode, enabled) VALUES (?,?,?,1)",
            (entity_id, tool_name, access_mode),
        )

    def remove_entity_tool(self, entity_id: int, tool_name: str) -> None:
        """Remove a tool assignment from an entity."""
        self._execute_write(
            "DELETE FROM entity_tools WHERE entity_id=? AND tool_name=?",
            (entity_id, tool_name),
        )

    def get_entity_tools(self, entity_id: int) -> list[dict]:
        """Return all tool assignments for an entity."""
        return [dict(r) for r in
                self.conn.execute(
                    "SELECT * FROM entity_tools WHERE entity_id=? AND enabled=1",
                    (entity_id,),
                ).fetchall()]

    def get_entity_tool(self, entity_id: int, tool_name: str) -> Optional[dict]:
        """Get a specific tool assignment for an entity."""
        row = self.conn.execute(
            "SELECT * FROM entity_tools WHERE entity_id=? AND tool_name=?",
            (entity_id, tool_name),
        ).fetchone()
        return dict(row) if row else None

    def get_shared_tools_for_discussion(self, discussion_id: int) -> list[dict]:
        """Return all shared-mode tool assignments for entities in a discussion."""
        return [dict(r) for r in
                self.conn.execute(
                    "SELECT et.* FROM entity_tools et "
                    "JOIN discussion_members dm ON et.entity_id = dm.entity_id "
                    "WHERE dm.discussion_id=? AND et.access_mode='shared' "
                    "AND et.enabled=1",
                    (discussion_id,),
                ).fetchall()]

    # ------------------------------------------------------------------
    # Discussion Tool Overrides
    # ------------------------------------------------------------------

    def set_discussion_tool_override(self, discussion_id: int, entity_id: int,
                                     tool_name: str, enabled: bool) -> None:
        """Set a per-discussion tool override."""
        self._execute_write(
            "INSERT OR REPLACE INTO discussion_tool_overrides "
            "(discussion_id, entity_id, tool_name, enabled) VALUES (?,?,?,?)",
            (discussion_id, entity_id, tool_name, int(enabled)),
        )

    def get_discussion_tool_overrides(self, discussion_id: int,
                                       entity_id: int) -> list[dict]:
        """Get tool overrides for an entity in a specific discussion."""
        return [dict(r) for r in
                self.conn.execute(
                    "SELECT * FROM discussion_tool_overrides "
                    "WHERE discussion_id=? AND entity_id=?",
                    (discussion_id, entity_id),
                ).fetchall()]
