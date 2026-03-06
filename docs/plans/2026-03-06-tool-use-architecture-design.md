# Pluggable Tool-Use Architecture for Consensus

## Context

Consensus currently has zero tool/function-calling support — the AI client sends text-only payloads and expects text-only responses. We want to enable AI participants to use tools (starting with web search, later memory, etc.) via a pluggable architecture that supports both Python-native tools and external MCP servers.

**User decisions captured during brainstorming:**
- Pluggable backends: abstract ToolProvider with Python + MCP implementations
- Native OpenAI function calling (tools parameter in chat completion API)
- Transparent tool use: shown inline as collapsible blocks
- Tool assignment: entity profile defaults + per-discussion overrides
- Three access modes: private, shared, moderator-only
- Refactor `_build_context()` to proper multi-message format (prerequisite)
- First tool: web search (Brave Search API with DuckDuckGo fallback)

## Architecture Overview

```
ConsensusApp
  └── ToolRegistry (aggregates all providers)
        ├── PythonToolProvider ("builtin")
        │     └── web_search tool
        └── MCPToolProvider ("mcp:server-name")  [future]
              └── tools discovered via MCP protocol
```

## Phase 1: Core Abstractions

### New file: `consensus/tools.py`

**Dataclasses:**
- `ToolDefinition(name, description, parameters: dict)` — JSON Schema for OpenAI tools format
- `ToolResult(content: str, is_error: bool, metadata: dict)` — result returned to LLM
- `ToolCallRecord(tool_name, arguments, result, is_error, latency_ms)` — persisted on Message

**Abstract base: `ToolProvider`**
```python
class ToolProvider(ABC):
    name: str
    async def list_tools(self) -> list[ToolDefinition]
    async def execute(self, tool_name: str, arguments: dict, context: ToolContext) -> ToolResult
    async def close(self) -> None
```

`ToolContext` carries: `caller_entity_id`, `discussion_id`, `access_mode`, enabling providers to namespace state.

**`PythonToolProvider`** — wraps Python callables (sync or async) registered via a dict mapping `name -> (callable, schema)`.

**`MCPToolProvider`** — connects to external MCP servers via the `mcp` Python SDK. Converts MCP tool schemas to `ToolDefinition`. Not implemented in phase 1, but the interface is ready.

**`ToolRegistry`** — held by `ConsensusApp`, aggregates providers:
- `get_tools_for_entity(entity_id, discussion_id) -> list[ToolDefinition]` — filtered by assignment, access mode, and discussion overrides
- `execute(tool_name, arguments, caller_entity_id, discussion) -> ToolResult` — dispatches to provider after access checks
- Tool execution timeout: 30s via `asyncio.wait_for()`

### Access Control (in ToolRegistry)

| Mode | Who sees the tool schema | Who can execute |
|------|------------------------|-----------------|
| **private** | Assigned entity only | Assigned entity only |
| **shared** | All entities in discussion | All entities in discussion |
| **moderator_only** | Moderator only | Moderator only |

Access checks happen in two places:
1. `get_tools_for_entity()` — controls what schemas are sent to the LLM
2. `execute()` — safety backstop if LLM hallucinates a tool call

## Phase 2: AI Client + Moderator Changes

### `consensus/ai_client.py`

Add `complete_with_tools()` method:
- Accepts optional `tools: list[dict]` parameter (OpenAI format)
- Includes `"tools": tools` in the payload when non-empty
- Returns the full `choices[0]["message"]` dict (may contain `tool_calls`)
- Add `tool_calls: list[ToolCallRecord]` field to `AIResponse`

### `consensus/moderator.py`

**Refactor `_build_context()`** to produce proper OpenAI message arrays:
```python
def _build_context(self, system_prompt: str, task: str) -> list[dict]:
    messages = [{"role": "system", "content": system_prompt}]
    for msg in self.discussion.messages[-CONTEXT_MESSAGE_LIMIT:]:
        messages.append({
            "role": "assistant" if msg.entity_id == current_entity_id else "user",
            "content": f"[{msg.entity_name}]: {msg.content}"
        })
    messages.append({"role": "user", "content": task})
    return messages
```

**Tool execution loop in `generate_turn()`:**
1. Get entity's allowed tools from `ToolRegistry`
2. Call `client.complete_with_tools(messages, tools=schemas)`
3. If response contains `tool_calls`:
   - Execute each via `registry.execute()`
   - Append assistant message (with tool_calls) + tool result messages to context
   - Collect `ToolCallRecord` objects
   - Re-call completion with extended messages
4. Repeat until `finish_reason == "stop"` or max iterations (5)
5. Return `AIResponse` with final text + accumulated `ToolCallRecord` list

**Safety limits:**
- `MAX_TOOL_ITERATIONS = 5`
- Per-tool execution timeout: 30s
- Tool errors passed back as tool results (model can recover)

Moderator synthesis methods (`generate_summary`, `generate_conclusion`, `mediate`) remain unchanged — no tool use for now.

## Phase 3: Data Model + Database

### `consensus/models.py`

Add to `Message`:
```python
tool_calls_json: str = ""  # JSON-serialized list of ToolCallRecord dicts
```

Update `Message.to_dict()` to include parsed `tool_calls` list.
Update `Message.from_db_row()` to read new column.

### `consensus/database.py`

**New tables:**

```sql
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
    FOREIGN KEY (discussion_id) REFERENCES discussions(id) ON DELETE CASCADE
);
```

**Migration:** Add `tool_calls_json TEXT` column to `messages` table.

**New CRUD methods:** `add_tool_provider()`, `get_tool_providers()`, `add_entity_tool()`, `remove_entity_tool()`, `get_entity_tools()`, `set_discussion_tool_override()`, `get_discussion_tool_overrides()`.

## Phase 4: App + API Integration

### `consensus/app.py`

- Create `ToolRegistry` in `__init__`, pass to `Moderator`
- Register built-in `PythonToolProvider` with web search automatically
- New methods: `register_tool_provider()`, `list_available_tools()`, `assign_tool_to_entity()`, `remove_entity_tool()`, `set_discussion_tool_override()`, `get_entity_tools()`
- Update `generate_ai_turn()` to persist `tool_calls_json` on messages
- Update `get_state()` to include tool configuration

### `consensus/server.py` + `consensus/desktop.py`

New API endpoints / bridge methods:
- `list_tools`, `get_entity_tools`, `assign_tool`, `remove_tool`, `set_tool_override`
- `register_tool_provider` (for MCP servers in future)

## Phase 5: Web Search Tool

### New file: `consensus/tools_builtin.py`

**`WebSearchProvider`** extending `PythonToolProvider`:
- Single tool: `web_search(query: str, num_results: int = 5)`
- Primary backend: **Brave Search API** (free tier: 2000 queries/month)
- Fallback: DuckDuckGo HTML API (no key required, less reliable)
- Uses existing `httpx` dependency
- API key via `BRAVE_SEARCH_API_KEY` env var (uses existing key infrastructure)

**Tool schema (OpenAI format):**
```json
{
  "type": "function",
  "function": {
    "name": "web_search",
    "description": "Search the web for current information.",
    "parameters": {
      "type": "object",
      "properties": {
        "query": {"type": "string", "description": "The search query"},
        "num_results": {"type": "integer", "description": "Results to return (1-10)", "default": 5}
      },
      "required": ["query"]
    }
  }
}
```

**Output format** (returned to LLM as tool result):
```
Search results for "query":
1. Title: ...
   URL: https://...
   Snippet: ...
```

## Phase 6: Frontend

### `consensus/static/app.js`

1. **Message rendering**: Tool calls shown as collapsible `<details>` blocks within AI messages
2. **Entity profile editor**: Add "Tools" section with checkboxes and access mode dropdowns
3. **Discussion setup**: Per-entity tool toggle overrides
4. **Tools settings tab**: View registered tool providers (future: add MCP servers)

## Files to Modify

| File | Changes |
|------|---------|
| `consensus/tools.py` | **NEW** — ToolProvider, ToolRegistry, dataclasses |
| `consensus/tools_builtin.py` | **NEW** — WebSearchProvider |
| `consensus/ai_client.py` | Add `complete_with_tools()`, extend `AIResponse` |
| `consensus/moderator.py` | Refactor `_build_context()`, add tool execution loop |
| `consensus/models.py` | Add `tool_calls_json` to Message |
| `consensus/database.py` | New tables, migration, CRUD methods |
| `consensus/app.py` | ToolRegistry integration, tool management methods |
| `consensus/server.py` | New API endpoints |
| `consensus/desktop.py` | New bridge methods |
| `consensus/static/app.js` | Tool display + configuration UI |

## Verification

1. **Unit test the tool execution loop**: Mock AIClient to return tool_calls, verify the loop executes tools and re-queries
2. **Integration test web search**: Register the web search tool, assign to an entity, run a discussion turn — verify tool calls appear in the response
3. **UI test**: Verify tool calls render as collapsible blocks in the discussion view
4. **Access control test**: Verify moderator-only tools aren't sent to participants, private tools are isolated
5. **Manual end-to-end**: Start a discussion with web search enabled, ask about a current event, verify the AI uses the tool and the results appear inline
