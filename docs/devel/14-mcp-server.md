# 14. MCP Server

[Back to index](programmer-manual.md) | [Previous: MCP Expert Plugins](13-mcp-expert-plugins.md)

---

Consensus exposes its data and operations to external AI agents (e.g. Claude
Code) via an MCP server. The server communicates over stdio using JSON-RPC 2.0
— the same protocol used by MCP clients, but reversed.

## Overview

```
External AI Agent (e.g. Claude Code)
    |
    | stdio JSON-RPC 2.0 (newline-delimited)
    |
    v
ConsensusMCPServer (mcp_server.py)
    |
    +-- Database (db/) — direct SQLite read/search/write
    |
    +-- EmbeddingClient (tools_memory.py) — semantic search via Ollama
    |
    +-- ConsensusApp (app.py) — discussion orchestration (run_discussion only)
```

## Entry Point

```toml
# pyproject.toml
[project.scripts]
consensus-mcp = "consensus.mcp_server:main"
```

```bash
# Run the server
consensus-mcp

# Configure in Claude Code MCP settings
{
  "mcpServers": {
    "consensus": { "command": "consensus-mcp" }
  }
}
```

## Protocol Handling

The server reads newline-delimited JSON-RPC from stdin and writes responses to
stdout. All logging goes to stderr.

**Methods handled:**

| Method | Response |
|--------|----------|
| `initialize` | Protocol version, capabilities `{"tools": {}}`, server info |
| `notifications/initialized` | None (notification) |
| `tools/list` | All 13 tool definitions |
| `tools/call` | Tool execution result |

The main loop uses `asyncio.get_event_loop().run_in_executor()` to read from
stdin in a thread, avoiding asyncio pipe transport issues on macOS.

## Tools

### Passive — List/Read (5 tools)

**`list_discussions`** — List discussions, optionally filtered by status.
- Parameters: `status?`, `limit?` (default 20)
- Implementation: `db.get_discussions()` with in-memory filtering

**`read_discussion`** — Read a discussion's full transcript and storyboard.
- Parameters: `discussion_id`, `include_messages?`, `include_storyboard?`
- Implementation: `app_discussion_state.get_export_data(db, discussion_id)`

**`list_entities`** — List entity profiles (AI, human, expert).
- Parameters: `entity_type?`
- Implementation: `db.get_entities(entity_type=...)`

**`list_documents`** — List all documents in the library.
- Implementation: `db.get_all_documents()`

**`read_document`** — Read a document's full text or a specific section.
- Parameters: `document_id`, `section_header?`
- Implementation: `db.get_document_markdown()` or section-based chunk retrieval

### Passive — Semantic Search (4 tools)

All semantic search tools require Ollama running with an embedding model.
They gracefully return error messages if the embedding service is unavailable.

**`search_discussions`** — Semantically search across all discussion messages.
- Parameters: `query`, `limit?`, `topic_filter?`
- Implementation: Embeds query via `EmbeddingClient`, ranks against
  `db.get_messages_with_embeddings()` using `_rank_by_similarity()`

**`search_memories`** — Search an entity's long-term memories.
- Parameters: `entity_id` (0 = own agent), `query`, `limit?`
- Implementation: `db.get_entity_memories_with_embeddings()` + cosine ranking

**`search_documents`** — Semantically search across all ingested documents.
- Parameters: `query`, `limit?`
- Implementation: `db.get_all_chunks_with_embeddings()` + cosine ranking

**`query_knowledge_graph`** — Query the KG by semantic search or node neighbors.
- Parameters: `query`, `mode?` (search/neighbors), `limit?`
- Implementation: `db.get_kg_nodes_with_embeddings()` for search,
  `db.get_kg_neighbors()` for neighbors mode

### Active — Write (4 tools)

**`store_memory`** — Store a persistent memory.
- Parameters: `content`, `entity_id?` (default 0 = own agent)
- Implementation: `db.add_entity_memory()` + async embedding via
  `EmbeddingClient`

**`delete_memory`** — Delete one of the agent's own memories.
- Parameters: `memory_id`
- **Ownership enforced:** Always deletes from the agent's own entity only.
  The `entity_id` parameter is not exposed. This is hardcoded — not
  configurable, not bypassable. The DB layer provides a second check
  (`delete_entity_memory` requires both `memory_id` and `entity_id` to match).

**`assert_knowledge`** — Assert a knowledge graph triple.
- Parameters: `subject`, `relation`, `object`, `description?`
- Implementation: Upserts subject and object nodes via
  `db.upsert_kg_node()`, creates edge via `db.add_kg_edge()`, embeds nodes

**`run_discussion`** — Create and run a full AI discussion.
- Parameters: `topic`, `entity_ids?`, `max_rounds?` (default 3),
  `moderator_id?`, `cost_limit?` (default $1.00)
- Implementation: Instantiates a fresh `ConsensusApp`, adds entities, starts
  discussion, runs `generate_ai_turn()` + `complete_turn()` loop, concludes,
  returns JSON with conclusion, cost, participants, rounds
- **Constraints:** Only AI entities allowed (humans rejected). 10-minute
  timeout. Auto-selects first 3 AI entities if none specified. Excludes the
  "Claude Code Agent" entity from auto-selection.

## Agent Entity

The server auto-creates an AI entity named "Claude Code Agent" on first use:

- `entity_id=0` is a parameter convention meaning "use my own entity"
- On first access, `_get_agent_entity_id()` finds or creates the entity via
  `db.add_entity()`, which returns a real auto-increment ID
- The resolved ID is cached in `self._agent_entity_id` for the session
- `store_memory` and `search_memories` with `entity_id=0` resolve to this entity
- `delete_memory` always operates on this entity (no parameter to change it)

## Code Reuse

The server reuses existing infrastructure:

| Need | Source |
|------|--------|
| Database access | `Database` class (`db/__init__.py`) — all 10 mixins |
| Embeddings | `EmbeddingClient` (`tools_memory.py`) |
| Similarity search | `_rank_by_similarity()` (`tools_memory.py`) |
| Embedding serialization | `_pack_embedding()` (`tools_memory.py`) |
| Discussion export | `get_export_data()` (`app_discussion_state.py`) |
| Discussion orchestration | `ConsensusApp` (`app.py`) |
| Data paths | `get_db_path()` (`config.py`) |

## Tests

Tests are in `tests/test_mcp_server.py` (28 tests):

- **Protocol tests:** `initialize`, `notifications/initialized`, `tools/list`,
  unknown method, unknown tool
- **List/read tool tests:** Empty and populated discussions, entity listing
  with type filter, document listing, discussion and document reading,
  not-found error handling
- **Write tool tests:** `store_memory` (including agent entity auto-creation),
  empty content rejection, `delete_memory` own vs other entity (ownership
  blocked), nonexistent memory, `assert_knowledge` success and missing fields
- **Agent entity tests:** Created once and reused, `entity_id=0` resolution,
  non-zero passthrough
- **`run_discussion` tests:** No entities available, human entity rejection,
  empty topic

---

[Back to index](programmer-manual.md)
