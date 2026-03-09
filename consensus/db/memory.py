"""Database mixin for memory, embeddings, and knowledge graph operations."""

import time
from typing import Optional

from ..models import (
    DEFAULT_EMBEDDING_BACKEND, DEFAULT_EMBEDDING_MODEL, DEFAULT_EMBEDDING_ENDPOINT,
)


class MemoryMixin:
    """Mixin providing memory, embedding, and knowledge graph database operations.

    Expects host class to provide:
        conn: sqlite3.Connection
        _lock: threading.Lock
        _execute_write(sql, params) -> sqlite3.Cursor
    """

    def _seed_default_memory_config(self) -> None:
        """Seed default memory_config values if the table exists."""
        has_table = self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='memory_config'"
        ).fetchone()
        if not has_table:
            return
        defaults = [
            ("embedding_backend", DEFAULT_EMBEDDING_BACKEND),
            ("embedding_model", DEFAULT_EMBEDDING_MODEL),
            ("embedding_endpoint", DEFAULT_EMBEDDING_ENDPOINT),
        ]
        with self._lock:
            for key, value in defaults:
                self.conn.execute(
                    "INSERT OR IGNORE INTO memory_config (key, value) VALUES (?,?)",
                    (key, value),
                )
            self.conn.commit()

    def get_memory_config(self) -> dict:
        """Return all memory_config rows as a dict."""
        rows = self.conn.execute("SELECT key, value FROM memory_config").fetchall()
        return {row[0]: row[1] for row in rows}

    def set_memory_config(self, key: str, value: str) -> None:
        """Upsert a memory_config value."""
        self._execute_write(
            "INSERT OR REPLACE INTO memory_config (key, value) VALUES (?,?)",
            (key, value),
        )

    def add_entity_memory(self, memory_id: str, entity_id: int,
                          content: str, discussion_id: Optional[int] = None) -> None:
        """Insert a new entity memory record."""
        self._execute_write(
            "INSERT INTO entity_memories (id, entity_id, content, discussion_id, created_at) "
            "VALUES (?,?,?,?,?)",
            (memory_id, entity_id, content, discussion_id, time.time()),
        )

    def set_entity_memory_embedding(self, memory_id: str, embedding: bytes) -> None:
        """Store or replace an embedding for an entity memory."""
        self._execute_write(
            "INSERT OR REPLACE INTO entity_memory_embeddings (memory_id, embedding) VALUES (?,?)",
            (memory_id, embedding),
        )

    def delete_entity_memory(self, memory_id: str, entity_id: int) -> bool:
        """Delete a memory. Returns True if a row was deleted."""
        cur = self._execute_write(
            "DELETE FROM entity_memories WHERE id=? AND entity_id=?",
            (memory_id, entity_id),
        )
        return cur.rowcount > 0

    def get_entity_memories_with_embeddings(
        self, entity_id: int
    ) -> list[dict]:
        """Return all (id, content, embedding) rows for an entity that have embeddings."""
        rows = self.conn.execute(
            "SELECT m.id, m.content, e.embedding "
            "FROM entity_memories m "
            "JOIN entity_memory_embeddings e ON e.memory_id = m.id "
            "WHERE m.entity_id = ?",
            (entity_id,),
        ).fetchall()
        return [{"id": r[0], "content": r[1], "embedding": r[2]} for r in rows]

    def set_message_embedding(self, message_id: str, embedding: bytes) -> None:
        """Store or replace an embedding for a message."""
        self._execute_write(
            "INSERT OR REPLACE INTO message_embeddings (message_id, embedding, indexed_at) "
            "VALUES (?,?,?)",
            (message_id, embedding, time.time()),
        )

    def get_unindexed_message_ids(self, discussion_id: int) -> list[str]:
        """Return message IDs from a discussion that have no embedding yet."""
        rows = self.conn.execute(
            "SELECT m.id FROM messages m "
            "LEFT JOIN message_embeddings e ON e.message_id = m.id "
            "WHERE m.discussion_id = ? AND e.message_id IS NULL",
            (discussion_id,),
        ).fetchall()
        return [str(r[0]) for r in rows]

    def get_message_content(self, message_id: str) -> Optional[str]:
        """Return content of a single message."""
        row = self.conn.execute(
            "SELECT content FROM messages WHERE id=?", (message_id,)
        ).fetchone()
        return row[0] if row else None

    def get_messages_with_embeddings(
        self, topic_filter: Optional[str] = None
    ) -> list[dict]:
        """Return (message_id, content, embedding, discussion_topic) rows."""
        if topic_filter:
            rows = self.conn.execute(
                "SELECT m.id, m.content, e.embedding, d.topic "
                "FROM messages m "
                "JOIN message_embeddings e ON e.message_id = m.id "
                "JOIN discussions d ON d.id = m.discussion_id "
                "WHERE d.topic LIKE ?",
                (f"%{topic_filter}%",),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT m.id, m.content, e.embedding, d.topic "
                "FROM messages m "
                "JOIN message_embeddings e ON e.message_id = m.id "
                "JOIN discussions d ON d.id = m.discussion_id",
            ).fetchall()
        return [{"id": r[0], "content": r[1], "embedding": r[2], "topic": r[3]}
                for r in rows]

    def upsert_kg_node(self, node_id: str, label: str, node_type: str,
                       description: Optional[str] = None) -> None:
        """Insert or update a knowledge graph node."""
        self._execute_write(
            "INSERT INTO kg_nodes (id, label, node_type, description, created_at) "
            "VALUES (?,?,?,?,?) "
            "ON CONFLICT(label) DO UPDATE SET description=excluded.description",
            (node_id, label, node_type, description, time.time()),
        )

    def get_kg_node_by_label(self, label: str) -> Optional[dict]:
        """Return a kg_node row by label."""
        row = self.conn.execute(
            "SELECT id, label, node_type, description FROM kg_nodes WHERE label=?",
            (label,),
        ).fetchone()
        return {"id": row[0], "label": row[1], "node_type": row[2], "description": row[3]} \
            if row else None

    def set_kg_node_embedding(self, node_id: str, embedding: bytes) -> None:
        """Store or replace an embedding for a kg node."""
        self._execute_write(
            "INSERT OR REPLACE INTO kg_node_embeddings (node_id, embedding) VALUES (?,?)",
            (node_id, embedding),
        )

    def add_kg_edge(self, edge_id: str, source_id: str, target_id: str,
                    relation: str, discussion_id: Optional[int] = None,
                    weight: float = 1.0) -> None:
        """Insert a knowledge graph edge."""
        self._execute_write(
            "INSERT OR IGNORE INTO kg_edges "
            "(id, source_id, target_id, relation, weight, discussion_id, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (edge_id, source_id, target_id, relation, weight, discussion_id, time.time()),
        )

    def get_kg_nodes_with_embeddings(self) -> list[dict]:
        """Return all kg nodes that have embeddings."""
        rows = self.conn.execute(
            "SELECT n.id, n.label, n.description, e.embedding "
            "FROM kg_nodes n "
            "JOIN kg_node_embeddings e ON e.node_id = n.id",
        ).fetchall()
        return [{"id": r[0], "label": r[1], "description": r[2], "embedding": r[3]}
                for r in rows]

    def get_kg_neighbors(self, node_id: str) -> list[dict]:
        """Return neighboring nodes and their edge relations."""
        rows = self.conn.execute(
            "SELECT n.label, e.relation, e.weight, 'out' as direction "
            "FROM kg_edges e JOIN kg_nodes n ON n.id = e.target_id "
            "WHERE e.source_id = ? "
            "UNION "
            "SELECT n.label, e.relation, e.weight, 'in' as direction "
            "FROM kg_edges e JOIN kg_nodes n ON n.id = e.source_id "
            "WHERE e.target_id = ?",
            (node_id, node_id),
        ).fetchall()
        return [{"label": r[0], "relation": r[1], "weight": r[2], "direction": r[3]}
                for r in rows]
