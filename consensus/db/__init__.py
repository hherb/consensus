"""SQLite persistence layer for entities, providers, discussions, and prompts.

The Database class composes domain-specific mixins for a clean separation
of concerns while preserving a single unified API surface.
"""

import os
import sqlite3
import threading
from typing import Optional

from ..migrator import run_migrations
from ..models import (
    DEFAULT_BASE_URL, DEFAULT_TEMPERATURE, DEFAULT_MAX_TOKENS, DEFAULT_AVATAR_COLOR,
    DEFAULT_EMBEDDING_BACKEND, DEFAULT_EMBEDDING_MODEL, DEFAULT_EMBEDDING_ENDPOINT,
)
from .prompts import PromptsMixin
from .providers import ProvidersMixin
from .entities import EntitiesMixin
from .discussions import DiscussionsMixin
from .messages import MessagesMixin
from .tools import ToolsMixin
from .mcp import MCPMixin
from .memory import MemoryMixin
from .documents import DocumentsMixin
from .images import ImagesMixin

_VALID_TABLES = frozenset({
    "providers", "entities", "prompts", "discussions",
})


class Database(
    PromptsMixin, ProvidersMixin, EntitiesMixin, DiscussionsMixin,
    MessagesMixin, ToolsMixin, MCPMixin, MemoryMixin, DocumentsMixin,
    ImagesMixin,
):
    """Thread-safe SQLite database for the consensus application.

    All write operations are serialized via a threading lock to prevent
    concurrent write errors when accessed from multiple threads (e.g.
    pywebview js_api calls).
    """

    def __init__(self, db_path: str) -> None:
        parent = os.path.dirname(db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._lock = threading.Lock()
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        # Load sqlite_vec extension if available (required for memory/embedding features)
        try:
            import sqlite_vec
            sqlite_vec.load(self.conn)
        except Exception:
            pass
        run_migrations(self.conn, self._lock, self.db_path)
        from ..pricing import PricingCache
        self.pricing = PricingCache(self.conn, self._lock)
        self._seed_default_prompts()
        self._seed_default_providers()
        self._migrate_providers()
        self._seed_default_memory_config()
        self._seed_devils_advocate_prompts()

    def _execute_write(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        """Execute a single write statement under the lock and commit."""
        with self._lock:
            cur = self.conn.execute(sql, params)
            self.conn.commit()
            return cur

    def _update_row(self, table: str, row_id: int,
                    allowed: set[str], extra_sets: Optional[dict] = None,
                    **kwargs: object) -> None:
        """Generic row update: filters kwargs to allowed fields, appends
        extra_sets (e.g. updated_at), and executes a single UPDATE.

        The table name is validated against _VALID_TABLES (module-level)
        to prevent SQL injection. If a new mixin needs _update_row for a
        table not yet listed, add it to _VALID_TABLES in this file."""
        if table not in _VALID_TABLES:
            raise ValueError(f"Invalid table: {table}")
        sets: list[str] = []
        vals: list[object] = []
        for k, v in kwargs.items():
            if k in allowed:
                sets.append(f"{k}=?")
                vals.append(v)
        if extra_sets:
            for k, v in extra_sets.items():
                sets.append(f"{k}=?")
                vals.append(v)
        if sets:
            vals.append(row_id)
            self._execute_write(
                f"UPDATE {table} SET {','.join(sets)} WHERE id=?", tuple(vals)
            )

    def close(self) -> None:
        """Close the database connection."""
        self.conn.close()
