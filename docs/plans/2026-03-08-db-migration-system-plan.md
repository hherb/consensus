# Database Migration System & Test Suite Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add comprehensive test coverage for database.py, then implement a file-based SQL migration system and refactor database.py to use it.

**Architecture:** A new `consensus/migrator.py` module scans `consensus/migrations/NNN_*.sql` files, tracks applied versions in a `migrations` table, and runs unapplied ones at startup. A module-level guard ensures single execution per db_path per process. The current inline schema/migration/seed code in database.py is replaced by a single `001_baseline.sql`.

**Tech Stack:** Python 3.12, sqlite3, pytest

---

### Task 1: Expand test suite — soft-delete discussions, restore, purge

**Files:**
- Modify: `tests/test_database.py`

**Step 1: Add soft-delete, restore, and purge tests to TestDiscussions**

Add these tests to the existing `TestDiscussions` class in `tests/test_database.py`:

```python
def test_soft_delete_discussion(self, tmp_db, sample_ai_entity):
    did = tmp_db.create_discussion("ToDelete", sample_ai_entity)
    count = tmp_db.soft_delete_discussions([did])
    assert count == 1
    d = tmp_db.get_discussion(did)
    assert d["deleted_at"] is not None
    # Should not appear in get_discussions list
    discussions = tmp_db.get_discussions()
    ids = [d["id"] for d in discussions]
    assert did not in ids

def test_soft_delete_multiple(self, tmp_db, sample_ai_entity):
    d1 = tmp_db.create_discussion("A", sample_ai_entity)
    d2 = tmp_db.create_discussion("B", sample_ai_entity)
    count = tmp_db.soft_delete_discussions([d1, d2])
    assert count == 2

def test_soft_delete_empty_list(self, tmp_db):
    assert tmp_db.soft_delete_discussions([]) == 0

def test_soft_delete_idempotent(self, tmp_db, sample_ai_entity):
    did = tmp_db.create_discussion("T", sample_ai_entity)
    tmp_db.soft_delete_discussions([did])
    count = tmp_db.soft_delete_discussions([did])
    assert count == 0  # already deleted

def test_restore_discussion(self, tmp_db, sample_ai_entity):
    did = tmp_db.create_discussion("T", sample_ai_entity)
    tmp_db.soft_delete_discussions([did])
    result = tmp_db.restore_discussion(did)
    assert result is True
    discussions = tmp_db.get_discussions()
    ids = [d["id"] for d in discussions]
    assert did in ids

def test_restore_non_deleted_discussion(self, tmp_db, sample_ai_entity):
    did = tmp_db.create_discussion("T", sample_ai_entity)
    result = tmp_db.restore_discussion(did)
    assert result is False

def test_purge_deleted_discussions(self, tmp_db, sample_ai_entity, sample_human_entity):
    did = tmp_db.create_discussion("T", sample_ai_entity)
    tmp_db.add_discussion_member(did, sample_ai_entity, True, False)
    tmp_db.add_message(did, sample_ai_entity, "msg", "moderator", 1)
    tmp_db.add_storyboard_entry(did, 1, "sum", sample_ai_entity)
    # Soft-delete, then backdate deleted_at
    tmp_db.soft_delete_discussions([did])
    tmp_db.conn.execute(
        "UPDATE discussions SET deleted_at = ? WHERE id = ?",
        (time.time() - 86400 * 30, did),
    )
    tmp_db.conn.commit()
    count = tmp_db.purge_deleted_discussions(max_days=7)
    assert count == 1
    assert tmp_db.get_discussion(did) is None
    assert tmp_db.get_messages(did) == []
    assert tmp_db.get_storyboard(did) == []
    assert tmp_db.get_discussion_members(did) == []
```

**Step 2: Run tests**

Run: `python -m pytest tests/test_database.py::TestDiscussions -v`
Expected: All PASS

**Step 3: Commit**

```bash
git add tests/test_database.py
git commit -m "test: add soft-delete, restore, purge discussion tests"
```

---

### Task 2: Expand test suite — discussion member roles and turn ordering

**Files:**
- Modify: `tests/test_database.py`

**Step 1: Add member role and ordering tests**

Add a new `TestDiscussionMembers` class:

```python
class TestDiscussionMembers:
    def test_add_member_with_role(self, tmp_db, sample_ai_entity):
        did = tmp_db.create_discussion("T", sample_ai_entity)
        tmp_db.add_discussion_member(did, sample_ai_entity, True, False,
                                     participant_role="devils_advocate")
        member = tmp_db.get_discussion_member(did, sample_ai_entity)
        assert member is not None
        assert member["participant_role"] == "devils_advocate"

    def test_update_member_role(self, tmp_db, sample_ai_entity):
        did = tmp_db.create_discussion("T", sample_ai_entity)
        tmp_db.add_discussion_member(did, sample_ai_entity, False, True,
                                     participant_role="standard")
        tmp_db.update_member_role(did, sample_ai_entity, "devils_advocate")
        member = tmp_db.get_discussion_member(did, sample_ai_entity)
        assert member["participant_role"] == "devils_advocate"

    def test_turn_position_ordering(self, tmp_db, sample_ai_entity, sample_human_entity):
        did = tmp_db.create_discussion("T", sample_ai_entity)
        tmp_db.add_discussion_member(did, sample_human_entity, False, True, turn_position=1)
        tmp_db.add_discussion_member(did, sample_ai_entity, True, False, turn_position=0)
        members = tmp_db.get_discussion_members(did)
        assert members[0]["entity_id"] == sample_ai_entity
        assert members[1]["entity_id"] == sample_human_entity

    def test_get_nonexistent_member(self, tmp_db, sample_ai_entity):
        did = tmp_db.create_discussion("T", sample_ai_entity)
        assert tmp_db.get_discussion_member(did, 99999) is None
```

**Step 2: Run tests**

Run: `python -m pytest tests/test_database.py::TestDiscussionMembers -v`
Expected: All PASS

**Step 3: Commit**

```bash
git add tests/test_database.py
git commit -m "test: add discussion member role and turn ordering tests"
```

---

### Task 3: Expand test suite — tool providers, entity tools, overrides

**Files:**
- Modify: `tests/test_database.py`

**Step 1: Add tool-related test classes**

```python
class TestToolProviders:
    def test_add_and_get(self, tmp_db):
        pid = tmp_db.add_tool_provider("web_search", "python")
        providers = tmp_db.get_tool_providers()
        names = [p["name"] for p in providers]
        assert "web_search" in names

    def test_add_duplicate_ignored(self, tmp_db):
        pid1 = tmp_db.add_tool_provider("web_search", "python")
        pid2 = tmp_db.add_tool_provider("web_search", "python")
        assert pid1 == pid2

    def test_delete_tool_provider(self, tmp_db):
        pid = tmp_db.add_tool_provider("temp", "python")
        tmp_db.delete_tool_provider(pid)
        providers = tmp_db.get_tool_providers()
        ids = [p["id"] for p in providers]
        assert pid not in ids


class TestEntityTools:
    def test_assign_and_get(self, tmp_db, sample_ai_entity):
        tmp_db.add_entity_tool(sample_ai_entity, "web_search", "private")
        tools = tmp_db.get_entity_tools(sample_ai_entity)
        assert len(tools) == 1
        assert tools[0]["tool_name"] == "web_search"
        assert tools[0]["access_mode"] == "private"

    def test_get_specific_tool(self, tmp_db, sample_ai_entity):
        tmp_db.add_entity_tool(sample_ai_entity, "web_search", "shared")
        tool = tmp_db.get_entity_tool(sample_ai_entity, "web_search")
        assert tool is not None
        assert tool["access_mode"] == "shared"

    def test_get_nonexistent_tool(self, tmp_db, sample_ai_entity):
        assert tmp_db.get_entity_tool(sample_ai_entity, "nope") is None

    def test_remove_entity_tool(self, tmp_db, sample_ai_entity):
        tmp_db.add_entity_tool(sample_ai_entity, "web_search")
        tmp_db.remove_entity_tool(sample_ai_entity, "web_search")
        assert tmp_db.get_entity_tools(sample_ai_entity) == []

    def test_shared_tools_for_discussion(self, tmp_db, sample_ai_entity):
        did = tmp_db.create_discussion("T", sample_ai_entity)
        tmp_db.add_discussion_member(did, sample_ai_entity, True, False)
        tmp_db.add_entity_tool(sample_ai_entity, "shared_tool", "shared")
        tmp_db.add_entity_tool(sample_ai_entity, "private_tool", "private")
        shared = tmp_db.get_shared_tools_for_discussion(did)
        names = [t["tool_name"] for t in shared]
        assert "shared_tool" in names
        assert "private_tool" not in names


class TestDiscussionToolOverrides:
    def test_set_and_get_override(self, tmp_db, sample_ai_entity):
        did = tmp_db.create_discussion("T", sample_ai_entity)
        tmp_db.set_discussion_tool_override(did, sample_ai_entity, "web_search", False)
        overrides = tmp_db.get_discussion_tool_overrides(did, sample_ai_entity)
        assert len(overrides) == 1
        assert overrides[0]["tool_name"] == "web_search"
        assert overrides[0]["enabled"] == 0

    def test_override_upsert(self, tmp_db, sample_ai_entity):
        did = tmp_db.create_discussion("T", sample_ai_entity)
        tmp_db.set_discussion_tool_override(did, sample_ai_entity, "web_search", False)
        tmp_db.set_discussion_tool_override(did, sample_ai_entity, "web_search", True)
        overrides = tmp_db.get_discussion_tool_overrides(did, sample_ai_entity)
        assert len(overrides) == 1
        assert overrides[0]["enabled"] == 1
```

**Step 2: Run tests**

Run: `python -m pytest tests/test_database.py::TestToolProviders tests/test_database.py::TestEntityTools tests/test_database.py::TestDiscussionToolOverrides -v`
Expected: All PASS

**Step 3: Commit**

```bash
git add tests/test_database.py
git commit -m "test: add tool provider, entity tool, and override tests"
```

---

### Task 4: Expand test suite — prompt filtering, entity filtering, generic helpers

**Files:**
- Modify: `tests/test_database.py`

**Step 1: Add prompt filtering and entity filtering tests**

Add to `TestPrompts`:

```python
def test_get_prompts_filter_by_role(self, tmp_db):
    prompts = tmp_db.get_prompts(role="moderator")
    assert all(p["role"] == "moderator" for p in prompts)
    assert len(prompts) > 0

def test_get_prompts_filter_by_target(self, tmp_db):
    prompts = tmp_db.get_prompts(target="ai")
    assert all(p["target"] == "ai" for p in prompts)

def test_get_prompts_filter_by_role_and_task(self, tmp_db):
    prompts = tmp_db.get_prompts(role="moderator", task="system")
    assert all(p["role"] == "moderator" and p["task"] == "system" for p in prompts)
```

Add to `TestEntities`:

```python
def test_get_entities_filter_by_type(self, tmp_db, sample_ai_entity, sample_human_entity):
    ai_entities = tmp_db.get_entities(entity_type="ai")
    assert all(e["entity_type"] == "ai" for e in ai_entities)
    human_entities = tmp_db.get_entities(entity_type="human")
    assert all(e["entity_type"] == "human" for e in human_entities)

def test_get_entities_include_inactive(self, tmp_db, sample_ai_entity):
    tmp_db.delete_entity(sample_ai_entity)
    entities = tmp_db.get_entities(include_inactive=True)
    ids = [e["id"] for e in entities]
    assert sample_ai_entity in ids

def test_get_inactive_entities(self, tmp_db, sample_ai_entity):
    # Force soft-delete by creating a reference first
    did = tmp_db.create_discussion("T", sample_ai_entity)
    tmp_db.add_discussion_member(did, sample_ai_entity, True, False)
    tmp_db.delete_entity(sample_ai_entity)
    inactive = tmp_db.get_inactive_entities()
    ids = [e["id"] for e in inactive]
    assert sample_ai_entity in ids
```

Add to `TestUpdateRow`:

```python
def test_update_row_filters_unknown_fields(self, tmp_db, sample_provider):
    # unknown_field should be silently ignored
    tmp_db._update_row("providers", sample_provider,
                       allowed={"name"}, name="X", unknown_field="Y")
    p = tmp_db.get_provider(sample_provider)
    assert p["name"] == "X"
```

**Step 2: Run tests**

Run: `python -m pytest tests/test_database.py -v`
Expected: All PASS

**Step 3: Commit**

```bash
git add tests/test_database.py
git commit -m "test: add prompt filtering, entity filtering, and helper tests"
```

---

### Task 5: Expand test suite — memory and knowledge graph (conditional)

**Files:**
- Modify: `tests/test_database.py`

**Step 1: Add memory/KG test class**

```python
try:
    import sqlite_vec  # noqa: F401
    HAS_SQLITE_VEC = True
except ImportError:
    HAS_SQLITE_VEC = False

@pytest.mark.skipif(not HAS_SQLITE_VEC, reason="sqlite_vec not installed")
class TestMemoryAndKG:
    def test_memory_config_get_set(self, tmp_db):
        config = tmp_db.get_memory_config()
        assert "embedding_backend" in config
        tmp_db.set_memory_config("test_key", "test_val")
        config = tmp_db.get_memory_config()
        assert config["test_key"] == "test_val"

    def test_add_entity_memory(self, tmp_db, sample_ai_entity):
        tmp_db.add_entity_memory("mem1", sample_ai_entity, "Remember this")
        # No direct getter for single memory, but we can verify via embeddings query
        # after setting an embedding
        tmp_db.set_entity_memory_embedding("mem1", b"\x00" * 16)
        memories = tmp_db.get_entity_memories_with_embeddings(sample_ai_entity)
        assert len(memories) == 1
        assert memories[0]["content"] == "Remember this"

    def test_delete_entity_memory(self, tmp_db, sample_ai_entity):
        tmp_db.add_entity_memory("mem2", sample_ai_entity, "Forget this")
        result = tmp_db.delete_entity_memory("mem2", sample_ai_entity)
        assert result is True

    def test_delete_nonexistent_memory(self, tmp_db, sample_ai_entity):
        result = tmp_db.delete_entity_memory("nope", sample_ai_entity)
        assert result is False

    def test_message_embedding(self, tmp_db, sample_ai_entity):
        did = tmp_db.create_discussion("T", sample_ai_entity)
        mid = tmp_db.add_message(did, sample_ai_entity, "test msg", "participant", 1)
        unindexed = tmp_db.get_unindexed_message_ids(did)
        assert str(mid) in unindexed
        tmp_db.set_message_embedding(str(mid), b"\x00" * 16)
        unindexed_after = tmp_db.get_unindexed_message_ids(did)
        assert str(mid) not in unindexed_after

    def test_get_message_content(self, tmp_db, sample_ai_entity):
        did = tmp_db.create_discussion("T", sample_ai_entity)
        mid = tmp_db.add_message(did, sample_ai_entity, "hello", "participant", 1)
        assert tmp_db.get_message_content(str(mid)) == "hello"
        assert tmp_db.get_message_content("99999") is None

    def test_get_messages_with_embeddings(self, tmp_db, sample_ai_entity):
        did = tmp_db.create_discussion("T", sample_ai_entity)
        mid = tmp_db.add_message(did, sample_ai_entity, "test", "participant", 1)
        tmp_db.set_message_embedding(str(mid), b"\x00" * 16)
        results = tmp_db.get_messages_with_embeddings()
        assert len(results) >= 1
        assert results[0]["content"] == "test"

    def test_get_messages_with_embeddings_topic_filter(self, tmp_db, sample_ai_entity):
        did = tmp_db.create_discussion("Unique Topic XYZ", sample_ai_entity)
        mid = tmp_db.add_message(did, sample_ai_entity, "test", "participant", 1)
        tmp_db.set_message_embedding(str(mid), b"\x00" * 16)
        results = tmp_db.get_messages_with_embeddings(topic_filter="Unique Topic")
        assert len(results) >= 1
        results_empty = tmp_db.get_messages_with_embeddings(topic_filter="NoMatch")
        assert len(results_empty) == 0

    def test_kg_node_upsert_and_get(self, tmp_db):
        tmp_db.upsert_kg_node("n1", "free will", "concept", "The ability to choose")
        node = tmp_db.get_kg_node_by_label("free will")
        assert node is not None
        assert node["description"] == "The ability to choose"

    def test_kg_node_not_found(self, tmp_db):
        assert tmp_db.get_kg_node_by_label("nonexistent") is None

    def test_kg_edge_and_neighbors(self, tmp_db):
        tmp_db.upsert_kg_node("n1", "A", "concept")
        tmp_db.upsert_kg_node("n2", "B", "concept")
        tmp_db.add_kg_edge("e1", "n1", "n2", "supports")
        neighbors = tmp_db.get_kg_neighbors("n1")
        assert len(neighbors) == 1
        assert neighbors[0]["label"] == "B"
        assert neighbors[0]["relation"] == "supports"
        assert neighbors[0]["direction"] == "out"
        # Reverse direction
        neighbors_rev = tmp_db.get_kg_neighbors("n2")
        assert len(neighbors_rev) == 1
        assert neighbors_rev[0]["direction"] == "in"

    def test_kg_nodes_with_embeddings(self, tmp_db):
        tmp_db.upsert_kg_node("n1", "test_node", "concept")
        tmp_db.set_kg_node_embedding("n1", b"\x00" * 16)
        nodes = tmp_db.get_kg_nodes_with_embeddings()
        assert len(nodes) >= 1
        assert nodes[0]["label"] == "test_node"
```

**Step 2: Run tests**

Run: `python -m pytest tests/test_database.py::TestMemoryAndKG -v`
Expected: All PASS (or all SKIP if sqlite_vec not installed)

**Step 3: Commit**

```bash
git add tests/test_database.py
git commit -m "test: add memory and knowledge graph tests"
```

---

### Task 6: Create migration infrastructure — migrator.py

**Files:**
- Create: `consensus/migrator.py`
- Create: `consensus/migrations/__init__.py` (empty)

**Step 1: Write migrator tests**

Add to `tests/test_database.py`:

```python
from consensus.migrator import run_migrations, _migrations_done
import consensus.migrator


class TestMigrator:
    def test_creates_migrations_table(self, tmp_path):
        """run_migrations creates the migrations table."""
        import sqlite3, threading
        db_path = str(tmp_path / "mig.db")
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        lock = threading.Lock()
        # Clear module-level guard
        consensus.migrator._migrations_done.discard(db_path)
        run_migrations(conn, lock, db_path)
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()]
        assert "migrations" in tables
        conn.close()

    def test_applies_sql_migrations(self, tmp_path):
        """Migrations from the migrations dir are applied in order."""
        import sqlite3, threading
        db_path = str(tmp_path / "mig.db")
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        lock = threading.Lock()
        consensus.migrator._migrations_done.discard(db_path)
        run_migrations(conn, lock, db_path)
        # After running, should have at least version 001
        row = conn.execute("SELECT MAX(version) FROM migrations").fetchone()
        assert row[0] >= 1
        conn.close()

    def test_idempotent_rerun(self, tmp_path):
        """Running migrations twice does not duplicate or fail."""
        import sqlite3, threading
        db_path = str(tmp_path / "mig.db")
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        lock = threading.Lock()
        consensus.migrator._migrations_done.discard(db_path)
        run_migrations(conn, lock, db_path)
        consensus.migrator._migrations_done.discard(db_path)
        run_migrations(conn, lock, db_path)  # should not fail
        rows = conn.execute("SELECT * FROM migrations").fetchall()
        versions = [r[0] for r in rows]
        assert len(versions) == len(set(versions))  # no duplicates
        conn.close()

    def test_single_execution_guard(self, tmp_path):
        """Second call with same db_path is a no-op."""
        import sqlite3, threading
        db_path = str(tmp_path / "mig.db")
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        lock = threading.Lock()
        consensus.migrator._migrations_done.discard(db_path)
        run_migrations(conn, lock, db_path)
        assert db_path in consensus.migrator._migrations_done
        # Second call should return immediately (no-op)
        run_migrations(conn, lock, db_path)
        conn.close()
```

**Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_database.py::TestMigrator -v`
Expected: FAIL (module doesn't exist yet)

**Step 3: Implement migrator.py**

Create `consensus/migrator.py`:

```python
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
                   db_path: str) -> None:
    if db_path in _migrations_done:
        return

    _ensure_migrations_table(conn, lock)

    is_legacy = _detect_legacy_schema(conn)
    if is_legacy:
        _stamp_baseline(conn, lock)

    applied = _get_applied_versions(conn)
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
```

Create `consensus/migrations/__init__.py` (empty file).

**Step 4: Run tests**

Run: `python -m pytest tests/test_database.py::TestMigrator -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add consensus/migrator.py consensus/migrations/__init__.py tests/test_database.py
git commit -m "feat: add file-based SQL migration runner"
```

---

### Task 7: Create 001_baseline.sql

**Files:**
- Create: `consensus/migrations/001_baseline.sql`

**Step 1: Write baseline SQL**

This file must contain all CREATE TABLE IF NOT EXISTS statements for every table in the current schema, plus INSERT OR IGNORE for seed data (default prompts, providers). The full schema includes: `providers`, `entities`, `prompts`, `discussions`, `discussion_members`, `messages`, `storyboard_entries`, `tool_providers`, `entity_tools`, `discussion_tool_overrides`. Memory tables (`entity_memories`, `entity_memory_embeddings`, `message_embeddings`, `kg_nodes`, `kg_node_embeddings`, `kg_edges`, `memory_config`) are also included with CREATE TABLE IF NOT EXISTS.

All columns must match the CURRENT schema (including `active` on entities, `deleted_at` on discussions, `participant_role` on discussion_members, `tool_calls_json` on messages).

Seed data: the same default prompts and providers currently in `_seed_default_prompts()`, `_seed_default_providers()`, and `_seed_devils_advocate_prompts()` — but using INSERT OR IGNORE with explicit IDs so they're idempotent.

**Step 2: Run full test suite**

Run: `python -m pytest tests/test_database.py -v`
Expected: All PASS (existing code still uses inline schema; baseline.sql is just a file on disk for now)

**Step 3: Commit**

```bash
git add consensus/migrations/001_baseline.sql
git commit -m "feat: add 001_baseline.sql with full current schema and seed data"
```

---

### Task 8: Refactor database.py to use migrator

**Files:**
- Modify: `consensus/database.py`
- Modify: `pyproject.toml`

**Step 1: Update pyproject.toml to include migration files**

Add `"migrations/*.sql"` to the package-data list:

```toml
[tool.setuptools.package-data]
consensus = ["static/*", "migrations/*.sql"]
```

**Step 2: Refactor Database.__init__**

In `database.py`:
1. Add `from .migrator import run_migrations` at the top
2. Replace the `_create_tables()` and all `_migrate_*()` and `_seed_*()` calls in `__init__` with a single `run_migrations(self.conn, self._lock, self.db_path)` call
3. Keep `_migrate_providers()` call AFTER `run_migrations()` (it needs Python runtime logic)
4. Delete these methods entirely:
   - `_create_tables()`
   - `_migrate_entity_active()`
   - `_migrate_discussion_paused()`
   - `_migrate_tools()`
   - `_migrate_discussion_deleted_at()`
   - `_migrate_memory()`
   - `_migrate_participant_role()`
   - `_seed_default_prompts()`
   - `_seed_default_providers()`
   - `_seed_devils_advocate_prompts()`

The new `__init__` should look like:

```python
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
    try:
        import sqlite_vec
        sqlite_vec.load(self.conn)
    except Exception:
        pass
    run_migrations(self.conn, self._lock, self.db_path)
    self._migrate_providers()
```

**Step 3: Also remove the SCHEMA_VERSION constant** (no longer used)

**Step 4: Run full test suite**

Run: `python -m pytest tests/test_database.py -v`
Expected: All PASS

**Step 5: Update TestDatabaseInit.test_schema_version_set**

This test checks for the old `schema_version` table. Update it to check the new `migrations` table instead:

```python
def test_migrations_tracked(self, tmp_db):
    row = tmp_db.conn.execute(
        "SELECT MAX(version) FROM migrations"
    ).fetchone()
    assert row[0] >= 1
```

**Step 6: Run full test suite again**

Run: `python -m pytest tests/test_database.py -v`
Expected: All PASS

**Step 7: Commit**

```bash
git add consensus/database.py consensus/migrator.py pyproject.toml tests/test_database.py
git commit -m "refactor: replace inline schema/migrations with file-based migration system"
```

---

### Task 9: Final validation and cleanup

**Files:**
- Verify: all files

**Step 1: Run full test suite**

Run: `python -m pytest tests/ -v`
Expected: All PASS

**Step 2: Verify database.py line count is reduced**

Run: `wc -l consensus/database.py`
Expected: ~1100-1200 lines (down from ~1580)

**Step 3: Verify the app starts correctly**

Run: `python -c "from consensus.database import Database; import tempfile, os; db = Database(os.path.join(tempfile.mkdtemp(), 'test.db')); print('OK:', len(db.get_prompts()), 'prompts'); db.close()"`
Expected: `OK: 10 prompts` (or similar count of seeded prompts)

**Step 4: Commit any remaining fixes**

```bash
git add -A
git commit -m "chore: final cleanup after migration system refactor"
```
