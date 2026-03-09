"""Tests for Database initialization and generic helpers."""

import sqlite3

import pytest

from consensus.database import Database
from consensus.models import DEFAULT_TEMPERATURE, DEFAULT_MAX_TOKENS


# --- Schema & initialization ---

class TestDatabaseInit:
    def test_tables_created(self, tmp_db):
        tables = [r[0] for r in tmp_db.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()]
        for t in ["providers", "entities", "prompts", "discussions",
                   "messages", "storyboard_entries", "discussion_members"]:
            assert t in tables, f"Missing table: {t}"

    def test_migrations_tracked(self, tmp_db):
        row = tmp_db.conn.execute(
            "SELECT MAX(version) FROM migrations"
        ).fetchone()
        assert row[0] >= 1

    def test_default_prompts_seeded(self, tmp_db):
        prompts = tmp_db.get_prompts()
        assert len(prompts) > 0
        roles = {p["role"] for p in prompts}
        assert "moderator" in roles
        assert "participant" in roles

    def test_foreign_keys_enabled(self, tmp_db):
        row = tmp_db.conn.execute("PRAGMA foreign_keys").fetchone()
        assert row[0] == 1

    def test_idempotent_init(self, tmp_path):
        """Creating a second Database on the same path should not duplicate data."""
        db_path = str(tmp_path / "test.db")
        db1 = Database(db_path)
        prompt_count_1 = len(db1.get_prompts())
        db1.conn.close()

        db2 = Database(db_path)
        prompt_count_2 = len(db2.get_prompts())
        db2.conn.close()
        assert prompt_count_1 == prompt_count_2


# --- _update_row safety ---

class TestUpdateRow:
    def test_rejects_invalid_table(self, tmp_db):
        with pytest.raises(ValueError, match="Invalid table"):
            tmp_db._update_row("users; DROP TABLE providers;--", 1, {"name"})

    def test_update_row_filters_unknown_fields(self, tmp_db, sample_provider):
        # unknown_field should be silently ignored
        tmp_db._update_row("providers", sample_provider,
                           allowed={"name"}, name="X", unknown_field="Y")
        p = tmp_db.get_provider(sample_provider)
        assert p["name"] == "X"
