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
    entity_type     TEXT NOT NULL CHECK('human','ai','expert'),
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
    tool_calls_json   TEXT DEFAULT '',  -- JSON array of ToolCallRecord dicts
    cost              REAL             -- USD cost of this message (NULL for human messages)
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

## Cost Tracking Tables

These tables support per-message cost tracking. Created via migration
`003_cost_tracking.sql`.

```sql
-- Cached model pricing from OpenRouter
model_pricing (
    model_id        TEXT PRIMARY KEY,
    prompt_cost     REAL NOT NULL DEFAULT 0,   -- cost per token (prompt)
    completion_cost REAL NOT NULL DEFAULT 0,   -- cost per token (completion)
    last_updated    REAL NOT NULL DEFAULT 0    -- Unix epoch of last refresh
)
```

The `messages.cost` column (added by the same migration) stores the calculated
USD cost per AI message.

## MCP Server and Expert Tables

These tables support the MCP expert plugins system. Created via migration
`004_mcp_experts.sql`.

```sql
-- Registered MCP server configurations
mcp_servers (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL DEFAULT '',
    command     TEXT NOT NULL,              -- executable path (e.g. "python", "node")
    args        TEXT NOT NULL DEFAULT '[]', -- JSON array of command-line arguments
    env         TEXT NOT NULL DEFAULT '{}', -- JSON object of environment variables
    enabled     INTEGER NOT NULL DEFAULT 1,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
)

-- Expert definitions: maps entities to MCP tools
expert_definitions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id           INTEGER NOT NULL UNIQUE REFERENCES entities(id),
    mcp_server_id       INTEGER NOT NULL REFERENCES mcp_servers(id),
    tool_name           TEXT NOT NULL,
    description         TEXT NOT NULL DEFAULT '',
    default_arguments   TEXT NOT NULL DEFAULT '{}', -- JSON object of default args
    query_param_name    TEXT NOT NULL DEFAULT 'query',
    timeout_seconds     INTEGER NOT NULL DEFAULT 30
)
```

The `entities.entity_type` CHECK constraint is expanded to include `'expert'`
in addition to `'human'` and `'ai'`.

## Memory Tables (Optional)

These tables support the institutional memory system. Created via the
`_migrate_memory()` migration, which only runs when `sqlite-vec` is installed
(the `[memory]` optional dependency group).

```sql
-- Per-entity long-term memories (observations, positions, reflections)
entity_memories (
    id            TEXT PRIMARY KEY,
    entity_id     TEXT NOT NULL REFERENCES entities(id),
    content       TEXT NOT NULL,
    discussion_id TEXT REFERENCES discussions(id),  -- provenance; NULL = manual
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
)

-- Embedding vectors stored separately from content
entity_memory_embeddings (
    memory_id     TEXT PRIMARY KEY REFERENCES entity_memories(id),
    embedding     BLOB NOT NULL    -- packed float32 array
)

-- Index of which messages have been embedded (for lazy indexing)
message_embeddings (
    message_id    TEXT PRIMARY KEY REFERENCES messages(id),
    embedding     BLOB NOT NULL,
    indexed_at    TEXT NOT NULL DEFAULT (datetime('now'))
)

-- Knowledge graph nodes
kg_nodes (
    id            TEXT PRIMARY KEY,
    label         TEXT NOT NULL UNIQUE,
    node_type     TEXT NOT NULL DEFAULT 'concept',  -- concept|position|claim|entity_ref
    description   TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
)

-- Knowledge graph node embeddings
kg_node_embeddings (
    node_id       TEXT PRIMARY KEY REFERENCES kg_nodes(id),
    embedding     BLOB NOT NULL
)

-- Knowledge graph edges (relationships between concepts)
kg_edges (
    id            TEXT PRIMARY KEY,
    source_id     TEXT NOT NULL REFERENCES kg_nodes(id),
    target_id     TEXT NOT NULL REFERENCES kg_nodes(id),
    relation      TEXT NOT NULL,  -- supports|contradicts|implies|is_a|relates_to|etc.
    weight        REAL NOT NULL DEFAULT 1.0,
    discussion_id TEXT REFERENCES discussions(id),
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
)

-- Memory subsystem configuration (embedding endpoint, model, etc.)
memory_config (
    key           TEXT PRIMARY KEY,
    value         TEXT NOT NULL
)
```

## Document Tables

These tables support the Document RAG system. Created via migration
`005_documents.sql`.

```sql
-- Ingested documents
documents (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    title           TEXT NOT NULL,
    source_type     TEXT NOT NULL CHECK('url', 'text', 'upload'),
    source_url      TEXT,
    mime_type       TEXT NOT NULL DEFAULT 'text/plain',
    full_text       TEXT NOT NULL DEFAULT '',
    char_count      INTEGER NOT NULL DEFAULT 0,
    chunk_count     INTEGER NOT NULL DEFAULT 0,
    summary         TEXT NOT NULL DEFAULT '',
    created_at      REAL NOT NULL
)

-- Text chunks for RAG retrieval
document_chunks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id     INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    chunk_index     INTEGER NOT NULL,
    content         TEXT NOT NULL,
    char_start      INTEGER NOT NULL,
    char_end        INTEGER NOT NULL
)

-- Chunk embeddings for similarity search
document_chunk_embeddings (
    chunk_id        INTEGER PRIMARY KEY REFERENCES document_chunks(id) ON DELETE CASCADE,
    embedding       BLOB NOT NULL
)

-- Many-to-many: documents attached to discussions
discussion_documents (
    discussion_id   INTEGER NOT NULL REFERENCES discussions(id) ON DELETE CASCADE,
    document_id     INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    added_at        REAL NOT NULL,
    PRIMARY KEY (discussion_id, document_id)
)
```

## Image Tables

These tables support image storage and multimodal context. Created via
migration `007_images.sql`.

```sql
-- Stored images
images (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    filename           TEXT NOT NULL,
    original_filename  TEXT NOT NULL DEFAULT '',
    title              TEXT NOT NULL DEFAULT '',
    description        TEXT NOT NULL DEFAULT '',
    mime_type          TEXT NOT NULL DEFAULT 'image/png',
    width              INTEGER,
    height             INTEGER,
    file_size          INTEGER NOT NULL DEFAULT 0,
    storage_path       TEXT NOT NULL,
    source_type        TEXT NOT NULL CHECK('upload', 'url', 'ai_generated'),
    source_url         TEXT,
    uploader_entity_id INTEGER,
    created_at         REAL NOT NULL
)

-- Many-to-many: images attached to discussions
discussion_images (
    discussion_id INTEGER NOT NULL REFERENCES discussions(id) ON DELETE CASCADE,
    image_id      INTEGER NOT NULL REFERENCES images(id) ON DELETE CASCADE,
    added_at      REAL NOT NULL,
    PRIMARY KEY (discussion_id, image_id)
)

-- Many-to-many: images attached to messages
message_images (
    message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    image_id   INTEGER NOT NULL REFERENCES images(id) ON DELETE CASCADE,
    PRIMARY KEY (message_id, image_id)
)
```

Image files are stored on disk in `<data_dir>/images/` with UUID-prefixed
filenames. The `storage_path` column stores the filename relative to the
images directory.

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

Migrations are handled by `migrator.py`, which auto-discovers numbered SQL
files in `consensus/migrations/` using the regex `^(\d{3})_.*\.sql$`. Each
migration is applied exactly once (tracked in a `migrations` table) in version
order. New migrations only need to be added as files — no registration required.

### Legacy inline migrations

The `Database` class includes several inline migration methods that run on
every construction (predating the file-based system):

| Migration | Purpose |
|-----------|---------|
| `_migrate_providers()` | Fixes a DeepSeek base URL issue; migrates literal API keys from DB into `~/.consensus/.env` |
| `_migrate_entity_active()` | Adds the `active` column for entity soft-delete |
| `_migrate_discussion_paused()` | Expands the `discussions.status` CHECK constraint to include `'paused'` |
| `_migrate_tools()` | Creates `tool_providers`, `entity_tools`, `discussion_tool_overrides` tables; adds `tool_calls_json` column to `messages` |
| `_migrate_memory()` | Creates `entity_memories`, `entity_memory_embeddings`, `message_embeddings`, `kg_nodes`, `kg_node_embeddings`, `kg_edges`, `memory_config` tables (requires `sqlite-vec`) |

### File-based migrations (`consensus/migrations/`)

| File | Purpose |
|------|---------|
| `001_baseline.sql` | Baseline schema for fresh databases |
| `002_max_rounds.sql` | Adds max rounds support to discussions |
| `003_cost_tracking.sql` | Creates `model_pricing` table; adds `cost` column to `messages` |
| `004_mcp_experts.sql` | Creates `mcp_servers` and `expert_definitions` tables; expands `entities.entity_type` CHECK to include `'expert'` |
| `005_documents.sql` | Creates `documents`, `document_chunks`, `document_chunk_embeddings`, `discussion_documents` tables |
| `006_discussion_methods.sql` | Adds discussion method configuration tables |
| `007_images.sql` | Creates `images`, `discussion_images`, `message_images` tables |

Migrations are idempotent -- they use `CREATE TABLE IF NOT EXISTS` and check
for the existence of columns before making changes.

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

### MCP server CRUD

| Method | Purpose |
|--------|---------|
| `add_mcp_server(name, description, command, args, env)` | Register an MCP server, returns dict |
| `get_mcp_servers()` | List all MCP servers |
| `update_mcp_server(server_id, **kwargs)` | Update server configuration |
| `delete_mcp_server(server_id)` | Remove a server and its expert definitions |

### Expert definition CRUD

| Method | Purpose |
|--------|---------|
| `save_expert_definition(entity_id, mcp_server_id, tool_name, ...)` | Create/update expert definition, returns dict |
| `get_expert_definitions()` | List all expert definitions (JOINs with `mcp_servers`) |
| `get_expert_definition_for_entity(entity_id)` | Get expert config for a specific entity |

### Discussion tool overrides

| Method | Purpose |
|--------|---------|
| `set_discussion_tool_override(discussion_id, entity_id, tool_name, enabled)` | Enable/disable tool per-discussion per-entity |
| `get_discussion_tool_overrides(discussion_id, entity_id)` | List overrides |

### Document CRUD (`db/documents.py`)

| Method | Purpose |
|--------|---------|
| `add_document(title, source_type, ...)` | Insert a document record, returns ID |
| `get_document(document_id)` | Return document dict |
| `delete_document(document_id)` | Delete document and its chunks |
| `add_document_chunk(document_id, chunk_index, content, ...)` | Add a text chunk |
| `add_discussion_document(discussion_id, document_id)` | Associate document with discussion |
| `remove_discussion_document(discussion_id, document_id)` | Remove association |
| `get_discussion_documents(discussion_id)` | List documents for a discussion |

### Image CRUD (`db/images.py`)

| Method | Purpose |
|--------|---------|
| `add_image(filename, original_filename, ...)` | Insert image record, returns ID |
| `get_image(image_id)` | Return image dict |
| `update_image_description(image_id, description)` | Update cached description |
| `update_image_title(image_id, title)` | Update title |
| `delete_image(image_id)` | Delete image record |
| `get_all_images()` | List all images (library) |
| `add_discussion_image(discussion_id, image_id)` | Associate image with discussion |
| `remove_discussion_image(discussion_id, image_id)` | Remove association |
| `get_discussion_images(discussion_id)` | List images for a discussion |
| `add_message_image(message_id, image_id)` | Associate image with message |
| `get_message_images(message_id)` | List images for a message |

---

[Next: Data Flow and Lifecycle](06-data-flow-and-lifecycle.md)
