# 5. Database Schema

[Back to index](programmer-manual.md) | [Previous: Frontend](04-frontend.md) | [Next: Data Flow and Lifecycle](06-data-flow-and-lifecycle.md)

---

All data is stored in a single SQLite database file. The `Database` class
manages schema creation, seeding, and migrations automatically on
construction.

## Core Tables

```sql
-- Version tracking
schema_version (version INTEGER)

-- AI API providers (e.g. OpenAI, Ollama, Anthropic)
providers (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    base_url    TEXT NOT NULL,
    api_key_env TEXT NOT NULL DEFAULT '',   -- env var name, NOT the key
    created_at  REAL NOT NULL
)

-- Participant profiles
entities (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    entity_type     TEXT NOT NULL CHECK('human','ai'),
    avatar_color    TEXT NOT NULL DEFAULT '#3b82f6',
    provider_id     INTEGER REFERENCES providers(id) ON DELETE SET NULL,
    model           TEXT,
    temperature     REAL DEFAULT 0.7,
    max_tokens      INTEGER DEFAULT 1024,
    system_prompt   TEXT DEFAULT '',
    active          INTEGER NOT NULL DEFAULT 1,  -- soft-delete flag
    created_at      REAL NOT NULL,
    updated_at      REAL NOT NULL
)

-- Customisable prompt templates
prompts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    role        TEXT NOT NULL CHECK('moderator','participant'),
    target      TEXT NOT NULL CHECK('ai','human'),
    task        TEXT NOT NULL,  -- system, turn, summarize, mediate, conclude, open, guidance
    content     TEXT NOT NULL,
    is_default  INTEGER NOT NULL DEFAULT 0,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
)

-- Discussion records
discussions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    topic           TEXT NOT NULL,
    moderator_id    INTEGER REFERENCES entities(id),
    started_at      REAL,
    ended_at        REAL,
    status          TEXT NOT NULL DEFAULT 'setup'
                    CHECK('setup','active','paused','concluded')
)

-- Many-to-many: which entities participate in which discussion
discussion_members (
    discussion_id    INTEGER REFERENCES discussions(id),
    entity_id        INTEGER REFERENCES entities(id),
    is_moderator     INTEGER NOT NULL DEFAULT 0,
    also_participant INTEGER NOT NULL DEFAULT 0,
    turn_position    INTEGER,           -- NULL = not in turn rotation
    PRIMARY KEY (discussion_id, entity_id)
)

-- All messages
messages (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    discussion_id     INTEGER REFERENCES discussions(id),
    entity_id         INTEGER REFERENCES entities(id),
    content           TEXT NOT NULL,
    role              TEXT NOT NULL CHECK('participant','moderator','system'),
    turn_number       INTEGER,
    timestamp         REAL NOT NULL,
    -- AI metadata (NULL for human messages)
    model_used        TEXT,
    prompt_tokens     INTEGER,
    completion_tokens INTEGER,
    total_tokens      INTEGER,
    latency_ms        INTEGER,
    temperature_used  REAL,
    prompt_id         INTEGER,
    tool_calls_json   TEXT DEFAULT ''  -- JSON array of ToolCallRecord dicts
)

-- Moderator summaries, indexed by turn
storyboard_entries (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    discussion_id       INTEGER REFERENCES discussions(id),
    turn_number         INTEGER NOT NULL,
    summary             TEXT NOT NULL,
    speaker_entity_id   INTEGER REFERENCES entities(id),
    timestamp           REAL NOT NULL
)
```

## Tool Tables

These tables manage the pluggable tool system. Created via the
`_migrate_tools()` migration.

```sql
-- Registered tool providers (Python in-process, future: MCP)
tool_providers (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,
    type        TEXT NOT NULL CHECK('python','mcp'),
    config_json TEXT DEFAULT '{}',
    enabled     INTEGER NOT NULL DEFAULT 1,
    created_at  REAL NOT NULL
)

-- Which tools are assigned to which entities
entity_tools (
    entity_id    INTEGER NOT NULL,
    tool_name    TEXT NOT NULL,
    access_mode  TEXT NOT NULL DEFAULT 'private'
                 CHECK('private','shared','moderator_only'),
    enabled      INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (entity_id, tool_name)
)

-- Per-discussion overrides for tool availability
discussion_tool_overrides (
    discussion_id  INTEGER NOT NULL,
    entity_id      INTEGER NOT NULL,
    tool_name      TEXT NOT NULL,
    enabled        INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (discussion_id, entity_id, tool_name)
)
```

All timestamps are Unix epoch floats (`time.time()`).

## Auth Tables (multi-user mode)

In multi-user mode (`--web --multi-user`), authentication data lives in a
**separate** `auth.db` file, managed by `AuthDatabase` in `auth.py`. This
keeps auth data isolated from per-session discussion databases.

```sql
-- User accounts
users (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    email           TEXT NOT NULL UNIQUE,
    password_hash   TEXT,            -- NULL for OAuth-only users
    display_name    TEXT NOT NULL DEFAULT '',
    avatar_url      TEXT NOT NULL DEFAULT '',
    oauth_provider  TEXT,            -- legacy; see user_oauth_identities
    oauth_id        TEXT,            -- legacy; see user_oauth_identities
    created_at      REAL NOT NULL,
    updated_at      REAL NOT NULL
)

-- Hashed auth tokens
auth_tokens (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash  TEXT NOT NULL UNIQUE,  -- SHA-256 of the raw token
    expires_at  REAL NOT NULL,
    created_at  REAL NOT NULL
)

-- Multiple OAuth identities per user
user_oauth_identities (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    provider    TEXT NOT NULL,
    oauth_id    TEXT NOT NULL,
    avatar_url  TEXT NOT NULL DEFAULT '',
    created_at  REAL NOT NULL,
    UNIQUE(provider, oauth_id)
)

-- CSRF state tokens for OAuth flow (10-minute TTL, single-use)
oauth_states (
    state       TEXT PRIMARY KEY,
    provider    TEXT NOT NULL,
    created_at  REAL NOT NULL
)
```

The `users` table retains legacy `oauth_provider` and `oauth_id` columns for
backwards compatibility. `user_oauth_identities` is the canonical source for
OAuth identity lookups and supports linking multiple providers to one account.

---

## Seeding

On first run, the database is seeded with default data:

### Default providers (5)

| Name | Base URL | API Key Env |
|------|----------|-------------|
| Ollama (Local) | `http://localhost:11434/v1` | *(none)* |
| Anthropic | `https://api.anthropic.com/v1` | `ANTHROPIC_API_KEY` |
| OpenAI | `https://api.openai.com/v1` | `OPENAI_API_KEY` |
| DeepSeek | `https://api.deepseek.com` | `DEEPSEEK_API_KEY` |
| Mistral | `https://api.mistral.ai/v1` | `MISTRAL_API_KEY` |

### Default prompts (9)

| Name | Role | Target | Task |
|------|------|--------|------|
| AI Moderator -- System | moderator | ai | system |
| AI Moderator -- Summarize | moderator | ai | summarize |
| AI Moderator -- Mediate | moderator | ai | mediate |
| AI Moderator -- Conclude | moderator | ai | conclude |
| AI Moderator -- Open | moderator | ai | open |
| AI Participant -- System | participant | ai | system |
| AI Participant -- Turn | participant | ai | turn |
| Human Moderator -- Guidance | moderator | human | guidance |
| Human Participant -- Guidance | participant | human | guidance |

Both seed methods check `COUNT(*)` first so they only run on an empty table.

---

## Migrations

The `Database` class includes several migration methods that run on every
construction:

| Migration | Purpose |
|-----------|---------|
| `_migrate_providers()` | Fixes a DeepSeek base URL issue; migrates literal API keys from DB into `~/.consensus/.env` |
| `_migrate_entity_active()` | Adds the `active` column for entity soft-delete |
| `_migrate_discussion_paused()` | Expands the `discussions.status` CHECK constraint to include `'paused'` |
| `_migrate_tools()` | Creates `tool_providers`, `entity_tools`, `discussion_tool_overrides` tables; adds `tool_calls_json` column to `messages` |

Migrations are idempotent -- they check for the existence of columns/tables
before making changes.

---

## CRUD Methods

The `Database` class provides methods grouped by table:

### Tool provider CRUD

| Method | Purpose |
|--------|---------|
| `add_tool_provider(name, type, config_json)` | Register a tool provider, returns ID |
| `get_tool_providers()` | List all providers |
| `delete_tool_provider(provider_id)` | Remove a provider |

### Entity-tool assignment CRUD

| Method | Purpose |
|--------|---------|
| `add_entity_tool(entity_id, tool_name, access_mode)` | Assign a tool to an entity |
| `remove_entity_tool(entity_id, tool_name)` | Remove assignment |
| `get_entity_tools(entity_id)` | List enabled tools for an entity |
| `get_entity_tool(entity_id, tool_name)` | Get single assignment |
| `get_shared_tools_for_discussion(discussion_id)` | List shared-mode tools for all members |

### Discussion tool overrides

| Method | Purpose |
|--------|---------|
| `set_discussion_tool_override(discussion_id, entity_id, tool_name, enabled)` | Enable/disable tool per-discussion per-entity |
| `get_discussion_tool_overrides(discussion_id, entity_id)` | List overrides |

---

[Next: Data Flow and Lifecycle](06-data-flow-and-lifecycle.md)
