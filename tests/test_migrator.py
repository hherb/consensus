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

    def test_legacy_schema_version_stamped(self, tmp_path):
        """Existing DB with schema_version table gets baseline stamped."""
        conn, db_path = self._fresh_conn(tmp_path)
        lock = threading.Lock()
        # Simulate a legacy database
        conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
        conn.execute("INSERT INTO schema_version (version) VALUES (1)")
        # Also create the tables that would exist in a legacy DB
        conn.execute("CREATE TABLE providers (id INTEGER PRIMARY KEY, name TEXT NOT NULL, base_url TEXT NOT NULL, api_key_env TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL)")
        conn.commit()
        consensus.migrator._migrations_done.discard(db_path)
        run_migrations(conn, lock, db_path)
        # schema_version should be gone
        sv = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_version'"
        ).fetchone()
        assert sv is None
        # baseline should be stamped
        row = conn.execute(
            "SELECT version FROM migrations WHERE version=1"
        ).fetchone()
        assert row is not None
        conn.close()
