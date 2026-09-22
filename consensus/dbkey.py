"""Scoping row ids to the database file that holds them.

Process-global bookkeeping (in-flight markers, failure records, "already
notified" sets) is keyed by row id in several tool packages. Row ids are
unique only *within one database*, and in ``--multi-user`` mode every browser
session gets its own SQLite file (``session.py``) — so every session's first
document, and every session's first discussion, is id 1.

Keyed by the bare id, one session's failed indexing pass made another
session's healthy document report as broken (issue #78 whole-branch review).
The same defect was still live for discussions in ``tools_memory``, so the
helper lives here once rather than in two copies (golden rule 1), in its own
module so that both tool packages can import it without importing each other.
"""

from typing import Union

# (db_path, row_id) — unique across sessions, unlike the bare row id.
ScopedKey = tuple[str, int]


def scoped_key(db: Union[str, object], row_id: int) -> ScopedKey:
    """Scope a row id to the database file that holds it.

    Args:
        db: A ``Database`` (any object carrying a ``db_path``), or the
            database path itself.
        row_id: The row id, unique only within that database.

    Returns:
        A ``(db_path, row_id)`` key that is unique across sessions.

    Raises:
        ValueError: If *db* carries no usable path. Defaulting to ``""``
            would produce a valid-looking key that collides across every
            session — silently reintroducing the bug this exists to prevent.
    """
    path = db if isinstance(db, str) else getattr(db, "db_path", "")
    if not path:
        raise ValueError(
            f"cannot scope row {row_id}: {type(db).__name__} carries no "
            "db_path"
        )
    return (str(path), row_id)
