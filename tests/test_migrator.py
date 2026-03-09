"""Tests for the database migration system."""

import sqlite3
import threading

import pytest

import consensus.migrator
from consensus.migrator import run_migrations


class TestMigrator:
    def _fresh_conn(self, tmp_path, name="mig.db"):
        db_path = str(tmp_path / name)
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        return conn, db_path

    def test_creates_migrations_table(self, tmp_path):
        conn, db_path = self._fresh_conn(tmp_path)
        lock = threading.Lock()
        consensus.migrator._migrations_done.discard(db_path)
        run_migrations(conn, lock, db_path)
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()]
        assert "migrations" in tables
        conn.close()

    def test_applies_sql_migrations(self, tmp_path):
        conn, db_path = self._fresh_conn(tmp_path)
        lock = threading.Lock()
        consensus.migrator._migrations_done.discard(db_path)
        run_migrations(conn, lock, db_path)
        row = conn.execute("SELECT MAX(version) FROM migrations").fetchone()
        assert row[0] >= 1
        conn.close()

    def test_creates_schema_tables(self, tmp_path):
        """Baseline migration creates all expected tables."""
        conn, db_path = self._fresh_conn(tmp_path)
        lock = threading.Lock()
        consensus.migrator._migrations_done.discard(db_path)
        run_migrations(conn, lock, db_path)
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()]
        for expected in ["providers", "entities", "prompts", "discussions",
                         "discussion_members", "messages", "storyboard_entries",
                         "tool_providers", "entity_tools",
                         "discussion_tool_overrides"]:
            assert expected in tables, f"Missing table: {expected}"
        conn.close()

    def test_idempotent_rerun(self, tmp_path):
        conn, db_path = self._fresh_conn(tmp_path)
        lock = threading.Lock()
        consensus.migrator._migrations_done.discard(db_path)
        run_migrations(conn, lock, db_path)
        consensus.migrator._migrations_done.discard(db_path)
        run_migrations(conn, lock, db_path)
        rows = conn.execute("SELECT * FROM migrations").fetchall()
        versions = [r[0] for r in rows]
        assert len(versions) == len(set(versions))  # no duplicates
        conn.close()

    def test_single_execution_guard(self, tmp_path):
        conn, db_path = self._fresh_conn(tmp_path)
        lock = threading.Lock()
        consensus.migrator._migrations_done.discard(db_path)
        run_migrations(conn, lock, db_path)
        assert db_path in consensus.migrator._migrations_done
        # Second call is a no-op
        run_migrations(conn, lock, db_path)
        conn.close()

