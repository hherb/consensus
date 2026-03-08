"""File-based SQL migration runner for consensus."""

import os
import re
import sqlite3
import threading
import time

_migrations_done: set[str] = set()

_MIGRATION_PATTERN = re.compile(r"^(\d{3})_.*\.sql$")


def _get_migrations_dir() -> str:
    return os.path.join(os.path.dirname(__file__), "migrations")


def _ensure_migrations_table(conn: sqlite3.Connection,
                             lock: threading.Lock) -> None:
    with lock:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS migrations (
                version     INTEGER PRIMARY KEY,
                name        TEXT NOT NULL,
                applied_at  REAL NOT NULL
            )
        """)
        conn.commit()


def _get_applied_versions(conn: sqlite3.Connection) -> set[int]:
    rows = conn.execute("SELECT version FROM migrations").fetchall()
    return {row[0] for row in rows}


def _detect_legacy_schema(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' "
        "AND name='schema_version'"
    ).fetchone()
    return row is not None


def _stamp_baseline(conn: sqlite3.Connection,
                    lock: threading.Lock) -> None:
    with lock:
        conn.execute(
            "INSERT OR IGNORE INTO migrations (version, name, applied_at) "
            "VALUES (?, ?, ?)",
            (1, "001_baseline.sql", time.time()),
        )
        conn.execute("DROP TABLE IF EXISTS schema_version")
        conn.commit()


def _discover_migrations(migrations_dir: str) -> list[tuple[int, str, str]]:
    if not os.path.isdir(migrations_dir):
        return []
    result = []
    for filename in sorted(os.listdir(migrations_dir)):
        m = _MIGRATION_PATTERN.match(filename)
        if m:
            version = int(m.group(1))
            path = os.path.join(migrations_dir, filename)
            result.append((version, filename, path))
    return result


def run_migrations(conn: sqlite3.Connection,
                   lock: threading.Lock,
                   db_path: str,
                   migrations_dir: str = "") -> None:
    """Apply any pending SQL migrations.

    Idempotent: tracks applied versions in a `migrations` table.
    Single-execution guard: skips if already run for this db_path
    in the current process.

    Args:
        migrations_dir: Directory containing .sql migration files.
            Defaults to consensus/migrations/ if not provided.

    For existing databases with the legacy `schema_version` table,
    stamps baseline version 001 as applied and drops the old table.
    """
    if db_path in _migrations_done:
        return

    _ensure_migrations_table(conn, lock)

    is_legacy = _detect_legacy_schema(conn)
    if is_legacy:
        _stamp_baseline(conn, lock)

    applied = _get_applied_versions(conn)
    if not migrations_dir:
        migrations_dir = _get_migrations_dir()
    pending = [
        (v, name, path)
        for v, name, path in _discover_migrations(migrations_dir)
        if v not in applied
    ]

    for version, name, path in pending:
        with open(path, "r") as f:
            sql = f.read()
        with lock:
            conn.executescript(sql)
            conn.execute(
                "INSERT INTO migrations (version, name, applied_at) "
                "VALUES (?, ?, ?)",
                (version, name, time.time()),
            )
            conn.commit()

    _migrations_done.add(db_path)
