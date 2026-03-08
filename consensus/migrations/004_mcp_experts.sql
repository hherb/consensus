-- MCP server registry
CREATE TABLE IF NOT EXISTS mcp_servers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    command TEXT NOT NULL,
    args TEXT NOT NULL DEFAULT '[]',
    env TEXT NOT NULL DEFAULT '{}',
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

-- Expert entity definitions linking entities to MCP server tools
CREATE TABLE IF NOT EXISTS expert_definitions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id INTEGER NOT NULL REFERENCES entities(id),
    mcp_server_id INTEGER NOT NULL REFERENCES mcp_servers(id),
    tool_name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    default_arguments TEXT NOT NULL DEFAULT '{}',
    timeout_seconds INTEGER NOT NULL DEFAULT 300,
    UNIQUE(entity_id)
);

-- Expand entity_type CHECK constraint to allow 'expert'.
-- SQLite does not support ALTER COLUMN, so we recreate the table.
CREATE TABLE entities_new (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    entity_type     TEXT NOT NULL DEFAULT 'ai' CHECK(entity_type IN ('human','ai','expert')),
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

INSERT INTO entities_new SELECT * FROM entities;
DROP TABLE entities;
ALTER TABLE entities_new RENAME TO entities;
