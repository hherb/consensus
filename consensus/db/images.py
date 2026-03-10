"""Database mixin for image storage and retrieval."""

import time
from typing import Optional


_IMAGE_COLUMNS = (
    "id", "filename", "original_filename", "title", "description",
    "mime_type", "width", "height", "file_size", "storage_path",
    "source_type", "source_url", "uploader_entity_id", "created_at",
)

_IMAGE_SELECT = (
    "id, filename, original_filename, title, description, "
    "mime_type, width, height, file_size, storage_path, "
    "source_type, source_url, uploader_entity_id, created_at"
)


def _row_to_dict(row) -> dict:
    """Convert an image row tuple to a dict."""
    return {col: row[i] for i, col in enumerate(_IMAGE_COLUMNS)}


class ImagesMixin:
    """Mixin providing image storage and retrieval operations.

    Expects host class to provide:
        conn: sqlite3.Connection
        _lock: threading.Lock
        _execute_write(sql, params) -> sqlite3.Cursor
    """

    # --- Image CRUD ---

    def add_image(
        self,
        filename: str,
        original_filename: str,
        title: str,
        description: str,
        mime_type: str,
        width: Optional[int],
        height: Optional[int],
        file_size: int,
        storage_path: str,
        source_type: str,
        source_url: Optional[str] = None,
        uploader_entity_id: Optional[int] = None,
    ) -> int:
        """Insert a new image and return its ID."""
        cur = self._execute_write(
            "INSERT INTO images "
            "(filename, original_filename, title, description, mime_type, "
            "width, height, file_size, storage_path, source_type, "
            "source_url, uploader_entity_id, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (filename, original_filename, title, description, mime_type,
             width, height, file_size, storage_path, source_type,
             source_url, uploader_entity_id, time.time()),
        )
        return cur.lastrowid

    def get_image(self, image_id: int) -> Optional[dict]:
        """Return an image row as dict, or None."""
        row = self.conn.execute(
            f"SELECT {_IMAGE_SELECT} FROM images WHERE id=?",
            (image_id,),
        ).fetchone()
        return _row_to_dict(row) if row else None

    def update_image_description(self, image_id: int, description: str) -> None:
        """Update the description for an image."""
        self._execute_write(
            "UPDATE images SET description=? WHERE id=?",
            (description, image_id),
        )

    def update_image_title(self, image_id: int, title: str) -> None:
        """Update the title for an image."""
        self._execute_write(
            "UPDATE images SET title=? WHERE id=?",
            (title, image_id),
        )

    def delete_image(self, image_id: int) -> bool:
        """Delete an image record. Returns True if deleted."""
        cur = self._execute_write(
            "DELETE FROM images WHERE id=?", (image_id,),
        )
        return cur.rowcount > 0

    def get_all_images(self) -> list[dict]:
        """Return all images (for library browsing)."""
        rows = self.conn.execute(
            f"SELECT {_IMAGE_SELECT} FROM images ORDER BY created_at DESC",
        ).fetchall()
        return [_row_to_dict(r) for r in rows]

    # --- Discussion-image association ---

    def add_discussion_image(self, discussion_id: int, image_id: int) -> None:
        """Associate an image with a discussion."""
        self._execute_write(
            "INSERT OR IGNORE INTO discussion_images "
            "(discussion_id, image_id, added_at) VALUES (?,?,?)",
            (discussion_id, image_id, time.time()),
        )

    def remove_discussion_image(self, discussion_id: int, image_id: int) -> None:
        """Remove an image from a discussion."""
        self._execute_write(
            "DELETE FROM discussion_images "
            "WHERE discussion_id=? AND image_id=?",
            (discussion_id, image_id),
        )

    def get_discussion_images(self, discussion_id: int) -> list[dict]:
        """Return all images attached to a discussion."""
        prefixed = ", ".join(f"i.{c}" for c in _IMAGE_COLUMNS)
        rows = self.conn.execute(
            f"SELECT {prefixed} "
            "FROM images i "
            "JOIN discussion_images di ON di.image_id = i.id "
            "WHERE di.discussion_id=? ORDER BY di.added_at",
            (discussion_id,),
        ).fetchall()
        return [_row_to_dict(r) for r in rows]

    # --- Message-image association ---

    def add_message_image(self, message_id: int, image_id: int) -> None:
        """Associate an image with a message."""
        self._execute_write(
            "INSERT OR IGNORE INTO message_images "
            "(message_id, image_id) VALUES (?,?)",
            (message_id, image_id),
        )

    def get_message_images(self, message_id: int) -> list[dict]:
        """Return all images attached to a message."""
        rows = self.conn.execute(
            "SELECT i.id, i.filename, i.title, i.description, i.mime_type, "
            "i.width, i.height "
            "FROM images i "
            "JOIN message_images mi ON mi.image_id = i.id "
            "WHERE mi.message_id=?",
            (message_id,),
        ).fetchall()
        return [
            {
                "id": r[0], "filename": r[1], "title": r[2],
                "description": r[3], "mime_type": r[4],
                "width": r[5], "height": r[6],
            }
            for r in rows
        ]
