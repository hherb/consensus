"""Database mixin for document storage and retrieval (RAG support)."""

import json
import time
from typing import Optional


class DocumentsMixin:
    """Mixin providing document storage and retrieval operations.

    Expects host class to provide:
        conn: sqlite3.Connection
        _lock: threading.Lock
        _execute_write(sql, params) -> sqlite3.Cursor
    """

    # --- Document CRUD ---

    def add_document(
        self,
        filename: str,
        title: str,
        summary: str,
        mime_type: str,
        source_type: str,
        source_url: Optional[str],
        markdown: str,
        char_count: int,
        sections_json: str,
    ) -> int:
        """Insert a new document and return its ID."""
        cur = self._execute_write(
            "INSERT INTO documents "
            "(filename, title, summary, mime_type, source_type, source_url, "
            "markdown, char_count, sections_json, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (filename, title, summary, mime_type, source_type, source_url,
             markdown, char_count, sections_json, time.time()),
        )
        return cur.lastrowid

    def get_document(self, doc_id: int) -> Optional[dict]:
        """Return a document row as dict, or None."""
        row = self.conn.execute(
            "SELECT id, filename, title, summary, mime_type, source_type, "
            "source_url, char_count, sections_json, created_at "
            "FROM documents WHERE id=?",
            (doc_id,),
        ).fetchone()
        if not row:
            return None
        return {
            "id": row[0], "filename": row[1], "title": row[2],
            "summary": row[3], "mime_type": row[4], "source_type": row[5],
            "source_url": row[6], "char_count": row[7],
            "sections_json": row[8], "created_at": row[9],
        }

    def get_document_markdown(self, doc_id: int) -> Optional[str]:
        """Return the full markdown text for a document."""
        row = self.conn.execute(
            "SELECT markdown FROM documents WHERE id=?", (doc_id,),
        ).fetchone()
        return row[0] if row else None

    def update_document_summary(self, doc_id: int, summary: str) -> None:
        """Update the summary for a document."""
        self._execute_write(
            "UPDATE documents SET summary=? WHERE id=?", (summary, doc_id),
        )

    def delete_document(self, doc_id: int) -> bool:
        """Delete a document and all associated chunks/embeddings. Returns True if deleted."""
        cur = self._execute_write(
            "DELETE FROM documents WHERE id=?", (doc_id,),
        )
        return cur.rowcount > 0

    def get_all_documents(self) -> list[dict]:
        """Return all documents (for full library search)."""
        rows = self.conn.execute(
            "SELECT id, filename, title, summary, mime_type, source_type, "
            "source_url, char_count, sections_json, created_at "
            "FROM documents ORDER BY created_at DESC",
        ).fetchall()
        return [
            {
                "id": r[0], "filename": r[1], "title": r[2],
                "summary": r[3], "mime_type": r[4], "source_type": r[5],
                "source_url": r[6], "char_count": r[7],
                "sections_json": r[8], "created_at": r[9],
            }
            for r in rows
        ]

    # --- Discussion-document association ---

    def add_discussion_document(self, discussion_id: int, document_id: int) -> None:
        """Associate a document with a discussion."""
        self._execute_write(
            "INSERT OR IGNORE INTO discussion_documents "
            "(discussion_id, document_id, added_at) VALUES (?,?,?)",
            (discussion_id, document_id, time.time()),
        )

    def remove_discussion_document(self, discussion_id: int, document_id: int) -> None:
        """Remove a document from a discussion."""
        self._execute_write(
            "DELETE FROM discussion_documents WHERE discussion_id=? AND document_id=?",
            (discussion_id, document_id),
        )

    def get_discussion_documents(self, discussion_id: int) -> list[dict]:
        """Return all documents attached to a discussion."""
        rows = self.conn.execute(
            "SELECT d.id, d.filename, d.title, d.summary, d.mime_type, "
            "d.source_type, d.source_url, d.char_count, d.created_at "
            "FROM documents d "
            "JOIN discussion_documents dd ON dd.document_id = d.id "
            "WHERE dd.discussion_id=? ORDER BY dd.added_at",
            (discussion_id,),
        ).fetchall()
        return [
            {
                "id": r[0], "filename": r[1], "title": r[2],
                "summary": r[3], "mime_type": r[4], "source_type": r[5],
                "source_url": r[6], "char_count": r[7], "created_at": r[8],
            }
            for r in rows
        ]

    def is_document_in_discussion(self, document_id: int, discussion_id: int) -> bool:
        """Check if a document is attached to a discussion."""
        row = self.conn.execute(
            "SELECT 1 FROM discussion_documents "
            "WHERE discussion_id=? AND document_id=?",
            (discussion_id, document_id),
        ).fetchone()
        return row is not None

    # --- Chunks ---

    def add_document_chunk(
        self,
        document_id: int,
        chunk_index: int,
        content: str,
        from_char: int,
        to_char: int,
        section_header: Optional[str] = None,
    ) -> int:
        """Insert a document chunk and return its ID."""
        cur = self._execute_write(
            "INSERT INTO document_chunks "
            "(document_id, chunk_index, content, from_char, to_char, section_header) "
            "VALUES (?,?,?,?,?,?)",
            (document_id, chunk_index, content, from_char, to_char, section_header),
        )
        return cur.lastrowid

    def get_document_chunks(self, document_id: int) -> list[dict]:
        """Return all chunks for a document, ordered by chunk_index."""
        rows = self.conn.execute(
            "SELECT id, chunk_index, content, from_char, to_char, section_header "
            "FROM document_chunks WHERE document_id=? ORDER BY chunk_index",
            (document_id,),
        ).fetchall()
        return [
            {
                "id": r[0], "chunk_index": r[1], "content": r[2],
                "from_char": r[3], "to_char": r[4], "section_header": r[5],
            }
            for r in rows
        ]

    def get_chunks_in_range(
        self, document_id: int, from_char: int, to_char: int
    ) -> list[dict]:
        """Return chunks that overlap with the given character range."""
        rows = self.conn.execute(
            "SELECT id, chunk_index, content, from_char, to_char, section_header "
            "FROM document_chunks "
            "WHERE document_id=? AND to_char > ? AND from_char < ? "
            "ORDER BY chunk_index",
            (document_id, from_char, to_char),
        ).fetchall()
        return [
            {
                "id": r[0], "chunk_index": r[1], "content": r[2],
                "from_char": r[3], "to_char": r[4], "section_header": r[5],
            }
            for r in rows
        ]

    def set_chunk_embedding(self, chunk_id: int, embedding: bytes) -> None:
        """Store or replace an embedding for a document chunk."""
        self._execute_write(
            "INSERT OR REPLACE INTO document_chunk_embeddings "
            "(chunk_id, embedding) VALUES (?,?)",
            (chunk_id, embedding),
        )

    def get_chunks_with_embeddings(self, document_id: int) -> list[dict]:
        """Return all chunks with embeddings for a document."""
        rows = self.conn.execute(
            "SELECT c.id, c.chunk_index, c.content, c.from_char, c.to_char, "
            "c.section_header, e.embedding "
            "FROM document_chunks c "
            "JOIN document_chunk_embeddings e ON e.chunk_id = c.id "
            "WHERE c.document_id=? ORDER BY c.chunk_index",
            (document_id,),
        ).fetchall()
        return [
            {
                "id": r[0], "chunk_index": r[1], "content": r[2],
                "from_char": r[3], "to_char": r[4], "section_header": r[5],
                "embedding": r[6],
            }
            for r in rows
        ]

    def get_all_chunks_with_embeddings(self) -> list[dict]:
        """Return all chunks with embeddings across all documents (for full library search)."""
        rows = self.conn.execute(
            "SELECT c.id, c.document_id, c.chunk_index, c.content, "
            "c.from_char, c.to_char, c.section_header, e.embedding, "
            "d.title, d.filename "
            "FROM document_chunks c "
            "JOIN document_chunk_embeddings e ON e.chunk_id = c.id "
            "JOIN documents d ON d.id = c.document_id "
            "ORDER BY c.document_id, c.chunk_index",
        ).fetchall()
        return [
            {
                "id": r[0], "document_id": r[1], "chunk_index": r[2],
                "content": r[3], "from_char": r[4], "to_char": r[5],
                "section_header": r[6], "embedding": r[7],
                "doc_title": r[8], "doc_filename": r[9],
            }
            for r in rows
        ]

    def count_unembedded_chunks(self, document_id: int) -> int:
        """Count chunks that don't have embeddings yet."""
        row = self.conn.execute(
            "SELECT COUNT(*) FROM document_chunks c "
            "LEFT JOIN document_chunk_embeddings e ON e.chunk_id = c.id "
            "WHERE c.document_id=? AND e.chunk_id IS NULL",
            (document_id,),
        ).fetchone()
        return row[0] if row else 0
