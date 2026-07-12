"""Database mixin for message and storyboard CRUD."""

import time
from typing import Optional


class MessagesMixin:
    """Mixin providing message and storyboard database operations.

    Expects host class to provide:
        conn: sqlite3.Connection
        _lock: threading.Lock
        _execute_write(sql, params) -> sqlite3.Cursor
    """

    def get_max_turn_number(self, discussion_id: int,
                            role: Optional[str] = None) -> int:
        """Return the highest turn_number from messages for a discussion.

        ``role`` restricts the maximum to messages with that role (e.g.
        ``"participant"``), ignoring system/moderator bookkeeping messages.
        """
        sql = "SELECT MAX(turn_number) FROM messages WHERE discussion_id=?"
        params: tuple = (discussion_id,)
        if role is not None:
            sql += " AND role=?"
            params = (discussion_id, role)
        row = self.conn.execute(sql, params).fetchone()
        return row[0] if row and row[0] is not None else 0

    def add_message(self, discussion_id: int, entity_id: int,
                    content: str, role: str, turn_number: int = 0,
                    model_used: str = "", prompt_tokens: int = 0,
                    completion_tokens: int = 0, total_tokens: int = 0,
                    latency_ms: int = 0,
                    temperature_used: Optional[float] = None,
                    prompt_id: int = 0,
                    tool_calls_json: str = "",
                    cost: Optional[float] = None,
                    timestamp: Optional[float] = None) -> int:
        """Store a message and return its generated ID."""
        cur = self._execute_write(
            "INSERT INTO messages "
            "(discussion_id,entity_id,content,role,turn_number,"
            "timestamp,model_used,prompt_tokens,completion_tokens,"
            "total_tokens,latency_ms,temperature_used,prompt_id,"
            "tool_calls_json,cost) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (discussion_id, entity_id, content, role, turn_number,
             timestamp if timestamp is not None else time.time(),
             model_used or None, prompt_tokens or None,
             completion_tokens or None, total_tokens or None,
             latency_ms or None,
             # Preserve an explicit temperature of 0.0 (deterministic); only
             # store NULL when no temperature was provided.
             temperature_used,
             prompt_id or None, tool_calls_json or None, cost),
        )
        return cur.lastrowid

    def get_messages(self, discussion_id: int) -> list[dict]:
        """Return all messages for a discussion with entity names, ordered by time."""
        return [dict(r) for r in
                self.conn.execute(
                    "SELECT m.*, e.name AS entity_name, e.avatar_color "
                    "FROM messages m "
                    "JOIN entities e ON m.entity_id=e.id "
                    "WHERE m.discussion_id=? ORDER BY m.timestamp, m.id",
                    (discussion_id,),
                ).fetchall()]

    def get_messages_windowed(self, discussion_id: int, limit: int,
                              offset: int = 0) -> list[dict]:
        """Return the last *limit* messages for a discussion (chronological).

        Uses a subquery to select the most recent rows, then re-sorts them
        in ascending timestamp order so the caller gets a natural sequence.
        """
        return [dict(r) for r in
                self.conn.execute(
                    "SELECT * FROM ("
                    "  SELECT m.*, e.name AS entity_name, e.avatar_color "
                    "  FROM messages m "
                    "  JOIN entities e ON m.entity_id=e.id "
                    "  WHERE m.discussion_id=? "
                    "  ORDER BY m.timestamp DESC, m.id DESC "
                    "  LIMIT ? OFFSET ?"
                    ") sub ORDER BY sub.timestamp, sub.id",
                    (discussion_id, limit, offset),
                ).fetchall()]

    def get_messages_count(self, discussion_id: int) -> int:
        """Return the total number of messages in a discussion."""
        row = self.conn.execute(
            "SELECT COUNT(*) FROM messages WHERE discussion_id=?",
            (discussion_id,),
        ).fetchone()
        return row[0] if row else 0

    def add_storyboard_entry(self, discussion_id: int, turn_number: int,
                             summary: str,
                             speaker_entity_id: int = 0) -> int:
        """Add a storyboard entry and return its auto-generated row ID."""
        cur = self._execute_write(
            "INSERT INTO storyboard_entries "
            "(discussion_id,turn_number,summary,speaker_entity_id,timestamp) "
            "VALUES (?,?,?,?,?)",
            (discussion_id, turn_number, summary,
             speaker_entity_id or None, time.time()),
        )
        return cur.lastrowid or 0

    def get_storyboard(self, discussion_id: int) -> list[dict]:
        """Return all storyboard entries for a discussion, ordered by time."""
        return [dict(r) for r in
                self.conn.execute(
                    "SELECT se.*, e.name AS speaker_name "
                    "FROM storyboard_entries se "
                    "LEFT JOIN entities e ON se.speaker_entity_id=e.id "
                    "WHERE se.discussion_id=? ORDER BY se.timestamp",
                    (discussion_id,),
                ).fetchall()]
