"""Database mixin for discussion and discussion member CRUD."""

import time
from typing import Optional

MAX_DAYS_KEEP_DELETED = 7


class DiscussionsMixin:
    """Mixin providing discussion and member database operations.

    Expects host class to provide:
        conn: sqlite3.Connection
        _lock: threading.Lock
        _execute_write(sql, params) -> sqlite3.Cursor
        _update_row(table, row_id, allowed, **kwargs) -> None
    """

    def create_discussion(self, topic: str,
                          moderator_id: int = 0) -> int:
        """Create a new discussion record. Returns the discussion ID."""
        mod_id = int(moderator_id) if moderator_id else None
        cur = self._execute_write(
            "INSERT INTO discussions (topic,moderator_id,started_at,status) "
            "VALUES (?,?,?,?)",
            (topic, mod_id, time.time(), "setup"),
        )
        return cur.lastrowid

    def get_discussions(self) -> list[dict]:
        """Return non-deleted discussions ordered by start time (newest first)."""
        return [dict(r) for r in
                self.conn.execute(
                    "SELECT * FROM discussions "
                    "WHERE deleted_at IS NULL "
                    "ORDER BY started_at DESC"
                ).fetchall()]

    def get_discussion(self, discussion_id: int) -> Optional[dict]:
        """Retrieve a single discussion by ID."""
        row = self.conn.execute(
            "SELECT * FROM discussions WHERE id=?", (discussion_id,)
        ).fetchone()
        return dict(row) if row else None

    def update_discussion(self, discussion_id: int, **kwargs: object) -> None:
        """Update a discussion's mutable fields."""
        self._update_row(
            "discussions", discussion_id,
            allowed={"topic", "moderator_id", "status", "ended_at", "started_at",
                     "max_rounds", "discussion_method", "method_state"},
            **kwargs,
        )

    def soft_delete_discussions(self, discussion_ids: list[int]) -> int:
        """Soft-delete discussions by setting deleted_at. Returns count deleted."""
        if not discussion_ids:
            return 0
        placeholders = ",".join("?" * len(discussion_ids))
        cur = self._execute_write(
            f"UPDATE discussions SET deleted_at = ? "
            f"WHERE id IN ({placeholders}) AND deleted_at IS NULL",
            (time.time(), *discussion_ids),
        )
        return cur.rowcount

    def restore_discussion(self, discussion_id: int) -> bool:
        """Restore a soft-deleted discussion."""
        cur = self._execute_write(
            "UPDATE discussions SET deleted_at = NULL WHERE id = ? AND deleted_at IS NOT NULL",
            (discussion_id,),
        )
        return cur.rowcount > 0

    def purge_deleted_discussions(self, max_days: int = MAX_DAYS_KEEP_DELETED) -> int:
        """Hard-delete discussions soft-deleted more than max_days ago.
        Cascades to messages, discussion_members, and storyboard_entries.
        Returns count of discussions purged.

        Uses explicit lock + conn.execute (not _execute_write) to keep
        all four deletes in a single atomic transaction.
        """
        cutoff = time.time() - (max_days * 86400)
        with self._lock:
            ids = [r[0] for r in self.conn.execute(
                "SELECT id FROM discussions WHERE deleted_at IS NOT NULL AND deleted_at < ?",
                (cutoff,),
            ).fetchall()]
            if not ids:
                return 0
            placeholders = ",".join("?" * len(ids))
            self.conn.execute(f"DELETE FROM storyboard_entries WHERE discussion_id IN ({placeholders})", ids)
            self.conn.execute(f"DELETE FROM messages WHERE discussion_id IN ({placeholders})", ids)
            self.conn.execute(f"DELETE FROM discussion_members WHERE discussion_id IN ({placeholders})", ids)
            self.conn.execute(f"DELETE FROM discussions WHERE id IN ({placeholders})", ids)
            self.conn.commit()
        return len(ids)

    def add_discussion_member(self, discussion_id: int, entity_id: int,
                              is_moderator: bool = False,
                              also_participant: bool = False,
                              turn_position: Optional[int] = None,
                              participant_role: str = "standard") -> None:
        """Add or update a discussion member record."""
        self._execute_write(
            "INSERT OR REPLACE INTO discussion_members "
            "(discussion_id,entity_id,is_moderator,also_participant,"
            "turn_position,participant_role) VALUES (?,?,?,?,?,?)",
            (discussion_id, entity_id, int(is_moderator),
             int(also_participant), turn_position, participant_role),
        )

    def get_discussion_members(self, discussion_id: int) -> list[dict]:
        """Return all members of a discussion with joined entity and provider info."""
        rows = self.conn.execute(
            "SELECT dm.entity_id AS id, dm.*, e.name, e.entity_type, "
            "e.avatar_color, e.provider_id, e.model, e.temperature, "
            "e.max_tokens, e.system_prompt, p.base_url, p.api_key_env "
            "FROM discussion_members dm "
            "JOIN entities e ON dm.entity_id=e.id "
            "LEFT JOIN providers p ON e.provider_id=p.id "
            "WHERE dm.discussion_id=? "
            "ORDER BY dm.turn_position NULLS LAST, e.name",
            (discussion_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_discussion_member(self, discussion_id: int,
                              entity_id: int) -> Optional[dict]:
        """Return a single discussion member record, or None."""
        row = self.conn.execute(
            "SELECT * FROM discussion_members "
            "WHERE discussion_id=? AND entity_id=?",
            (discussion_id, entity_id),
        ).fetchone()
        return dict(row) if row else None

    def update_member_role(self, discussion_id: int, entity_id: int,
                           role: str) -> None:
        """Update the participant_role of a discussion member."""
        self._execute_write(
            "UPDATE discussion_members SET participant_role=? "
            "WHERE discussion_id=? AND entity_id=?",
            (role, discussion_id, entity_id),
        )

    def remove_discussion_member(self, discussion_id: int,
                                 entity_id: int) -> None:
        """Remove a member from a discussion."""
        self._execute_write(
            "DELETE FROM discussion_members "
            "WHERE discussion_id=? AND entity_id=?",
            (discussion_id, entity_id),
        )
