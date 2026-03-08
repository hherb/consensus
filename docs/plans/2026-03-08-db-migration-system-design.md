# Database Migration System & Test Suite Design

**Date:** 2026-03-08
**Status:** Approved

## Problem

`database.py` has grown to ~1580 lines. Schema creation, inline migrations, and seed data are mixed with CRUD logic. Adding new migrations requires editing Python code rather than dropping in SQL files. There is no test suite to verify database behavior during refactoring.

## Goals

1. Comprehensive test suite for all database.py functionality
2. File-based migration system using numbered SQL files
3. Refactor database.py to remove inline schema/migration code

## Migration System

### Version tracking table

```sql
CREATE TABLE IF NOT EXISTS migrations (
    version     INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    applied_at  REAL NOT NULL
);
```

Replaces the current single-row `schema_version` table.

### Migration files

Location: `consensus/migrations/NNN_descriptive_name.sql`

- Bundled inside the Python package (not user-editable)
- Sequential integer versioning: `001`, `002`, `003`...
- Included via `package_data` in `pyproject.toml`

### Migrator module (`consensus/migrator.py`)

```
run_migrations(conn, lock) -> None
    1. Ensure `migrations` table exists
    2. Query max applied version
    3. Scan consensus/migrations/ for .sql files
    4. Sort by version number
    5. For each unapplied migration:
       a. Read SQL content
       b. Execute under lock
       c. Record in migrations table
       d. Commit
```

**Single-execution guarantee:** A module-level `_migrations_done` set (keyed by db_path) ensures migrations run only once per database per process, even if multiple `Database` instances are created (e.g. multi-user session manager).

### Baseline handling

`001_baseline.sql` contains the full current schema (all tables with current columns) plus seed data (default prompts, providers).

- **Existing databases:** Migrator detects old `schema_version` table, stamps 001 as applied without executing, drops old table.
- **Fresh databases:** Runs `001_baseline.sql` normally.

### Provider data fixes

The `_migrate_providers()` logic (DeepSeek URL fix, Mistral addition, literal API key migration) requires Python runtime logic (`save_api_key`). This remains as a Python post-baseline hook called from `Database.__init__()`.

## Test Suite

### Location

`tests/test_database.py` using `pytest`.

### Fixture

```python
@pytest.fixture
def db(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    yield db
    db.close()
```

### Test classes

| Class | Coverage |
|-------|----------|
| `TestSchemaCreation` | Tables exist, migrations table set, idempotent re-init |
| `TestProviders` | add, get, get_all, update, delete |
| `TestEntities` | add, get, get_all (active/inactive), update, delete (hard+soft), reactivate |
| `TestPrompts` | seeded defaults, CRUD, get_by_task, filtering |
| `TestDiscussions` | create, get, update, soft-delete, restore, purge cascade |
| `TestDiscussionMembers` | add, get, update role, remove, turn ordering |
| `TestMessages` | add with all fields, get with entity join, max turn number |
| `TestStoryboard` | add entry, get with speaker join |
| `TestToolProviders` | add (dedup), get, delete |
| `TestEntityTools` | assign, get, remove, shared tools query |
| `TestDiscussionToolOverrides` | set, get overrides |
| `TestMemoryAndKG` | config, memories CRUD, embeddings, KG nodes/edges/neighbors (skip if no sqlite_vec) |
| `TestGenericHelpers` | _update_row valid/invalid tables, field filtering |

## Refactoring Plan

### Removed from database.py (~400 lines)

- `_create_tables()` — replaced by `001_baseline.sql`
- `_migrate_entity_active()`, `_migrate_discussion_paused()`, `_migrate_tools()`, `_migrate_discussion_deleted_at()`, `_migrate_memory()`, `_migrate_participant_role()` — all covered by baseline
- `_seed_default_prompts()`, `_seed_default_providers()`, `_seed_devils_advocate_prompts()` — seed data in baseline SQL

### Kept in database.py

- `_migrate_providers()` — runtime data fixes requiring Python
- All CRUD methods unchanged

### New files

| File | Purpose |
|------|---------|
| `consensus/migrator.py` | Migration runner |
| `consensus/migrations/001_baseline.sql` | Full current schema + seed data |
| `tests/test_database.py` | Comprehensive test suite |

### Execution order

1. Write test suite against current database.py — verify all pass
2. Create migrator.py + 001_baseline.sql
3. Refactor database.py to use migrator
4. Re-run tests — verify all still pass
