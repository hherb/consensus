# Institutional Memory — Design Document

**Date:** 2026-03-07
**Status:** Approved for implementation

## Context

AI participants in Consensus currently have no memory beyond the current discussion's message window. Each discussion starts fresh — prior positions, reasoning, and insights are lost. This design adds persistent, searchable institutional memory as optional tools that AI entities can be assigned, giving them:

1. **Long-term personal memory** — store and recall observations/positions across discussions
2. **Semantic search** — retrieve relevant passages from the corpus of past discussions
3. **Knowledge graph** — assert and query structured concept/relationship triples

Memory is opt-in per entity (assigned via the existing tool assignment UI), keeping it invisible to entities that don't need it.

---

## Architecture Overview

A single new module `consensus/tools_memory.py` implements a `MemoryToolProvider` that extends the existing `PythonToolProvider` pattern from `tools.py`. Registered in `app.py` alongside the builtin tools provider, guarded by a `try: import sqlite_vec` check.

```
consensus/tools_memory.py          ← new: MemoryToolProvider + all 6 tools
consensus/database.py              ← extended: _migrate_memory() with 5 new tables
pyproject.toml                     ← extended: [memory] optional dep group
consensus/static/index.html        ← extended: Memory Config section in Settings tab
consensus/static/app.js            ← extended: memory config UI + API calls
consensus/app.py                   ← extended: _init_memory_tools() call
```

---

## Data Model

Five new SQLite tables added by `_migrate_memory()` in `database.py`:

```sql
-- Per-entity long-term memories (episodic observations, positions, reflections)
CREATE TABLE IF NOT EXISTS entity_memories (
    id           TEXT PRIMARY KEY,
    entity_id    TEXT NOT NULL REFERENCES entities(id),
    content      TEXT NOT NULL,
    discussion_id TEXT REFERENCES discussions(id),  -- provenance; NULL = manually stored
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Embeddings stored separately for clean separation from content
-- (sqlite_vec virtual shadow tables created alongside these)
CREATE TABLE IF NOT EXISTS entity_memory_embeddings (
    memory_id    TEXT PRIMARY KEY REFERENCES entity_memories(id),
    embedding    BLOB NOT NULL
);

-- Index of which messages have been embedded (for lazy indexing)
CREATE TABLE IF NOT EXISTS message_embeddings (
    message_id   TEXT PRIMARY KEY REFERENCES messages(id),
    embedding    BLOB NOT NULL,
    indexed_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Knowledge graph nodes
CREATE TABLE IF NOT EXISTS kg_nodes (
    id           TEXT PRIMARY KEY,
    label        TEXT NOT NULL UNIQUE,
    node_type    TEXT NOT NULL DEFAULT 'concept',  -- concept|position|claim|entity_ref
    description  TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS kg_node_embeddings (
    node_id      TEXT PRIMARY KEY REFERENCES kg_nodes(id),
    embedding    BLOB NOT NULL
);

-- Knowledge graph edges
CREATE TABLE IF NOT EXISTS kg_edges (
    id           TEXT PRIMARY KEY,
    source_id    TEXT NOT NULL REFERENCES kg_nodes(id),
    target_id    TEXT NOT NULL REFERENCES kg_nodes(id),
    relation     TEXT NOT NULL,  -- supports|contradicts|implies|is_a|relates_to|etc.
    weight       REAL NOT NULL DEFAULT 1.0,
    discussion_id TEXT REFERENCES discussions(id),
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Memory subsystem configuration
CREATE TABLE IF NOT EXISTS memory_config (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
-- Default rows: embedding_backend=ollama, embedding_model=nomic-embed-text,
--               embedding_endpoint=http://localhost:11434
```

`sqlite_vec` virtual tables are created alongside `entity_memory_embeddings`, `message_embeddings`, and `kg_node_embeddings` for ANN similarity search.

---

## Tools

Six tools registered by `MemoryToolProvider`, all available in the `"memory"` namespace:

| Tool | Description | Parameters |
|------|-------------|-----------|
| `memory_store` | Store an observation/position to this entity's long-term memory | `content: str` |
| `memory_recall` | Semantic search over this entity's own memories | `query: str`, `limit?: int (default 5)` |
| `memory_forget` | Delete a specific memory by ID | `memory_id: str` |
| `discussion_search` | Semantic search over all past discussion messages | `query: str`, `limit?: int (default 5)`, `topic_filter?: str` |
| `kg_assert` | Assert a knowledge triple (creates nodes + edge if not exists) | `subject: str`, `relation: str`, `object: str`, `description?: str` |
| `kg_query` | Query the knowledge graph | `query: str`, `mode: "search"\|"neighbors"\|"path"`, `target?: str` |

`ToolContext.caller_entity_id` is used to automatically scope `memory_store`, `memory_recall`, and `memory_forget` to the calling entity — no entity ID parameter needed.

---

## Embedding Pipeline

**Backend:** Ollama HTTP API via `httpx` (already a core dependency).
**Default model:** `nomic-embed-text` (768-dim, fast, good general-purpose retrieval).
**Endpoint:** Configurable in `memory_config`, default `http://localhost:11434/api/embeddings`.

**Embedding generation:**
- `memory_store` → embed immediately, store in `entity_memory_embeddings`
- `kg_assert` → embed node label+description on creation/update
- Past messages → indexed lazily on first `discussion_search` call for that discussion; background async batch via `asyncio.create_task()`

**Similarity search:** `sqlite_vec` vector similarity (cosine distance) for all three search surfaces.

**Graceful degradation:** If ollama is unreachable, tools return an error message and the entity's turn continues without memory access. Memory tools never crash the discussion.

---

## Integration Points

### `app.py`
- Add `_init_memory_tools(self)` method, called from `__init__` after `_init_builtin_tools()`
- Guard with `try: import sqlite_vec; from .tools_memory import MemoryToolProvider`
- Creates `MemoryToolProvider(db=self.db)` and registers with `self.tool_registry`

### `database.py`
- Add `_migrate_memory(self)` called from `_migrate()`
- Handles `sqlite_vec` extension loading (`conn.enable_load_extension(True)` + `sqlite_vec.load(conn)`)

### `pyproject.toml`
```toml
[project.optional-dependencies]
memory = ["sqlite-vec>=0.1.0", "numpy>=1.26"]
all = ["pywebview>=5.0", "aiohttp>=3.9", "trafilatura>=1.6", "sqlite-vec>=0.1.0", "numpy>=1.26"]
```

### Frontend
- **Profiles tab** — Memory tools (`memory_store`, `memory_recall`, etc.) appear automatically in the existing `entity-tools-section` checkbox list once registered
- **Settings tab** — New "Memory" accordion section with fields: embedding endpoint URL, model name, test connection button
- New `/api/memory/config` GET/PUT endpoints in `server.py`

### `moderator.py`
- No changes required — memory access is entirely tool-driven via the existing tool execution loop

---

## Optional Dependency Installation

```bash
uv pip install -e ".[memory]"    # memory features only
uv pip install -e ".[all]"       # everything
```

Users who don't install `[memory]` get no memory tools — `app.py` silently skips `_init_memory_tools()`.

---

## Verification

1. Install with `uv pip install -e ".[memory]"`
2. Start ollama with `ollama pull nomic-embed-text`
3. Run `python -m consensus`
4. In Profiles tab, create an AI entity and assign memory tools
5. Start a discussion — the AI should be able to call `memory_store`, `memory_recall`, `kg_assert`, `kg_query`, `discussion_search`
6. End discussion, start new one with same entity — confirm `memory_recall` returns memories from prior discussion
7. Run multiple discussions — confirm `discussion_search` returns semantically relevant messages cross-discussion
8. Test `memory_forget` removes a memory from subsequent `memory_recall` results
9. Test graceful degradation: stop ollama, confirm discussion continues without crashing
