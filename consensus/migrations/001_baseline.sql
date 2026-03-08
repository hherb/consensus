-- 001_baseline.sql
-- Full schema baseline for the consensus application.
-- All tables use CREATE TABLE IF NOT EXISTS for idempotency.

CREATE TABLE IF NOT EXISTS providers (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    base_url    TEXT NOT NULL,
    api_key_env TEXT NOT NULL DEFAULT '',
    created_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS entities (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    entity_type     TEXT NOT NULL CHECK(entity_type IN ('human','ai')),
    avatar_color    TEXT NOT NULL DEFAULT '#3b82f6',
    provider_id     INTEGER,
    model           TEXT,
    temperature     REAL DEFAULT 0.7,
    max_tokens      INTEGER DEFAULT 1024,
    system_prompt   TEXT DEFAULT '',
    created_at      REAL NOT NULL,
    updated_at      REAL NOT NULL,
    active          INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY (provider_id) REFERENCES providers(id)
        ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS prompts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    role        TEXT NOT NULL CHECK(role IN ('moderator','participant')),
    target      TEXT NOT NULL CHECK(target IN ('ai','human')),
    task        TEXT NOT NULL,
    content     TEXT NOT NULL,
    is_default  INTEGER NOT NULL DEFAULT 0,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS discussions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    topic           TEXT NOT NULL,
    moderator_id    INTEGER,
    started_at      REAL,
    ended_at        REAL,
    status          TEXT NOT NULL DEFAULT 'setup'
        CHECK(status IN ('setup','active','paused','concluded')),
    deleted_at      REAL,
    FOREIGN KEY (moderator_id) REFERENCES entities(id)
);

CREATE TABLE IF NOT EXISTS discussion_members (
    discussion_id       INTEGER NOT NULL,
    entity_id           INTEGER NOT NULL,
    is_moderator        INTEGER NOT NULL DEFAULT 0,
    also_participant    INTEGER NOT NULL DEFAULT 0,
    turn_position       INTEGER,
    participant_role    TEXT NOT NULL DEFAULT 'standard',
    PRIMARY KEY (discussion_id, entity_id),
    FOREIGN KEY (discussion_id) REFERENCES discussions(id),
    FOREIGN KEY (entity_id)     REFERENCES entities(id)
);

CREATE TABLE IF NOT EXISTS messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    discussion_id   INTEGER NOT NULL,
    entity_id       INTEGER NOT NULL,
    content         TEXT NOT NULL,
    role            TEXT NOT NULL
        CHECK(role IN ('participant','moderator','system')),
    turn_number     INTEGER,
    timestamp       REAL NOT NULL,
    model_used      TEXT,
    prompt_tokens   INTEGER,
    completion_tokens INTEGER,
    total_tokens    INTEGER,
    latency_ms      INTEGER,
    temperature_used REAL,
    prompt_id       INTEGER,
    tool_calls_json TEXT,
    FOREIGN KEY (discussion_id) REFERENCES discussions(id),
    FOREIGN KEY (entity_id)     REFERENCES entities(id)
);

CREATE TABLE IF NOT EXISTS storyboard_entries (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    discussion_id       INTEGER NOT NULL,
    turn_number         INTEGER NOT NULL,
    summary             TEXT NOT NULL,
    speaker_entity_id   INTEGER,
    timestamp           REAL NOT NULL,
    FOREIGN KEY (discussion_id)     REFERENCES discussions(id),
    FOREIGN KEY (speaker_entity_id) REFERENCES entities(id)
);

-- Tool system tables

CREATE TABLE IF NOT EXISTS tool_providers (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,
    type        TEXT NOT NULL CHECK(type IN ('python', 'mcp')),
    config_json TEXT NOT NULL DEFAULT '{}',
    enabled     INTEGER NOT NULL DEFAULT 1,
    created_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS entity_tools (
    entity_id       INTEGER NOT NULL,
    tool_name       TEXT NOT NULL,
    access_mode     TEXT NOT NULL DEFAULT 'private'
        CHECK(access_mode IN ('private', 'shared', 'moderator_only')),
    enabled         INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (entity_id, tool_name),
    FOREIGN KEY (entity_id) REFERENCES entities(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS discussion_tool_overrides (
    discussion_id   INTEGER NOT NULL,
    entity_id       INTEGER NOT NULL,
    tool_name       TEXT NOT NULL,
    enabled         INTEGER NOT NULL,
    PRIMARY KEY (discussion_id, entity_id, tool_name),
    FOREIGN KEY (discussion_id) REFERENCES discussions(id) ON DELETE CASCADE,
    FOREIGN KEY (entity_id) REFERENCES entities(id) ON DELETE CASCADE
);

-- Institutional memory tables (work with or without sqlite_vec extension)

CREATE TABLE IF NOT EXISTS entity_memories (
    id            TEXT PRIMARY KEY,
    entity_id     INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    content       TEXT NOT NULL,
    discussion_id INTEGER REFERENCES discussions(id),
    created_at    REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS entity_memory_embeddings (
    memory_id  TEXT PRIMARY KEY
        REFERENCES entity_memories(id) ON DELETE CASCADE,
    embedding  BLOB NOT NULL
);

CREATE TABLE IF NOT EXISTS message_embeddings (
    message_id TEXT PRIMARY KEY
        REFERENCES messages(id) ON DELETE CASCADE,
    embedding  BLOB NOT NULL,
    indexed_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS kg_nodes (
    id          TEXT PRIMARY KEY,
    label       TEXT NOT NULL UNIQUE,
    node_type   TEXT NOT NULL DEFAULT 'concept',
    description TEXT,
    created_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS kg_node_embeddings (
    node_id   TEXT PRIMARY KEY
        REFERENCES kg_nodes(id) ON DELETE CASCADE,
    embedding BLOB NOT NULL
);

CREATE TABLE IF NOT EXISTS kg_edges (
    id            TEXT PRIMARY KEY,
    source_id     TEXT NOT NULL REFERENCES kg_nodes(id),
    target_id     TEXT NOT NULL REFERENCES kg_nodes(id),
    relation      TEXT NOT NULL,
    weight        REAL NOT NULL DEFAULT 1.0,
    discussion_id INTEGER REFERENCES discussions(id),
    created_at    REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS memory_config (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Seed data (providers, prompts, memory config) is handled by Python
-- code in database.py after migrations run, since the content involves
-- complex strings and runtime logic that are better expressed in Python.
