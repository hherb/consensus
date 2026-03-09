"""Database mixin for entity profile CRUD."""

import sqlite3
import time
from typing import Optional

from ..models import DEFAULT_AVATAR_COLOR, DEFAULT_TEMPERATURE, DEFAULT_MAX_TOKENS


class EntitiesMixin:
    """Mixin providing entity profile database operations.

    Expects host class to provide:
        conn: sqlite3.Connection
        _lock: threading.Lock
        _execute_write(sql, params) -> sqlite3.Cursor
        _update_row(table, row_id, allowed, extra_sets, **kwargs) -> None
    """

    def add_entity(self, name: str, entity_type: str,
                   avatar_color: str = DEFAULT_AVATAR_COLOR,
                   provider_id: int = 0, model: str = "",
                   temperature: float = DEFAULT_TEMPERATURE,
                   max_tokens: int = DEFAULT_MAX_TOKENS,
                   system_prompt: str = "") -> int:
        """Add a new entity profile. Returns the new entity ID."""
        now = time.time()
        prov_id = int(provider_id) if provider_id else None
        cur = self._execute_write(
            "INSERT INTO entities "
            "(name,entity_type,avatar_color,provider_id,model,"
            "temperature,max_tokens,system_prompt,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (name, entity_type, avatar_color,
             prov_id, model, temperature, max_tokens,
             system_prompt, now, now),
        )
        return cur.lastrowid

    def get_entities(self, entity_type: str = "",
                     include_inactive: bool = False) -> list[dict]:
        """Return entities with joined provider info, optionally filtered by type."""
        base = (
            "SELECT e.*, p.name AS provider_name, p.base_url, "
            "p.api_key_env FROM entities e "
            "LEFT JOIN providers p ON e.provider_id=p.id"
        )
        conditions = []
        params: list[object] = []
        if not include_inactive:
            conditions.append("e.active=1")
        if entity_type:
            conditions.append("e.entity_type=?")
            params.append(entity_type)
        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
        rows = self.conn.execute(
            f"{base}{where} ORDER BY e.name", tuple(params),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_inactive_entities(self) -> list[dict]:
        """Return only inactive (soft-deleted) entities."""
        rows = self.conn.execute(
            "SELECT e.*, p.name AS provider_name, p.base_url, "
            "p.api_key_env FROM entities e "
            "LEFT JOIN providers p ON e.provider_id=p.id "
            "WHERE e.active=0 ORDER BY e.name",
        ).fetchall()
        return [dict(r) for r in rows]

    def get_entity(self, entity_id: int) -> Optional[dict]:
        """Retrieve a single entity with joined provider info."""
        row = self.conn.execute(
            "SELECT e.*, p.name AS provider_name, p.base_url, "
            "p.api_key_env FROM entities e "
            "LEFT JOIN providers p ON e.provider_id=p.id "
            "WHERE e.id=?",
            (entity_id,),
        ).fetchone()
        return dict(row) if row else None

    def update_entity(self, entity_id: int, **kwargs: object) -> None:
        """Update an entity's mutable fields."""
        self._update_row(
            "entities", entity_id,
            allowed={
                "name", "entity_type", "avatar_color", "provider_id",
                "model", "temperature", "max_tokens", "system_prompt",
            },
            extra_sets={"updated_at": time.time()},
            **kwargs,
        )

    def delete_entity(self, entity_id: int) -> dict:
        """Delete an entity by ID, or deactivate if referenced by past discussions.

        Returns {"deleted": True} or {"deactivated": True}.
        """
        try:
            self._execute_write(
                "DELETE FROM entities WHERE id=?", (entity_id,))
            return {"deleted": True}
        except sqlite3.IntegrityError:
            self._execute_write(
                "UPDATE entities SET active=0, updated_at=? WHERE id=?",
                (time.time(), entity_id))
            return {"deactivated": True}

    def reactivate_entity(self, entity_id: int) -> bool:
        """Reactivate a soft-deleted entity."""
        self._execute_write(
            "UPDATE entities SET active=1, updated_at=? WHERE id=?",
            (time.time(), entity_id))
        return True
