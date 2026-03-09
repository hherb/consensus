# 8. Tool Use Architecture

[Back to index](programmer-manual.md) | [Previous: API Reference](07-api-reference.md) | [Next: Prompts, Providers, and Security](09-prompts-providers-security.md)

---

Consensus supports **pluggable tool use** during AI turn generation. When an
AI entity has tools assigned, the moderator's generation loop allows the LLM
to call tools iteratively before producing its final text response.

## Overview

```
ConsensusApp
    |
    +-- ToolRegistry (tools.py)
    |     |
    |     +-- PythonToolProvider ("builtin")
    |     |     |
    |     |     +-- web_search (tools_builtin.py)
    |     |     +-- consult_expert (app.py)
    |     |
    |     +-- PythonToolProvider ("memory")   [optional, requires sqlite-vec]
    |     |     |
    |     |     +-- memory_store, memory_recall, memory_forget
    |     |     +-- discussion_search
    |     |     +-- kg_assert, kg_query
    |     |
    |     +-- MCPToolProvider (mcp_client.py)  [per registered MCP server]
    |           |
    |           +-- Tools discovered dynamically via MCP tools/list
    |
    +-- Moderator (moderator.py)
          |
          +-- generate_turn() tool execution loop
                |
                +-- AIClient.complete_with_tools()
                +-- ToolRegistry.execute()
```

## Core Concepts

### ToolDefinition

Schema wrapper for a tool in OpenAI function-calling format:

| Field | Type | Purpose |
|-------|------|---------|
| `name` | `str` | Unique tool identifier |
| `description` | `str` | Human-readable description (sent to LLM) |
| `parameters` | `dict` | JSON Schema for the tool's arguments |
| `provider_name` | `str` | Which provider offers this tool |

`to_openai_schema()` converts to the format expected by the OpenAI API:
```python
{
    "type": "function",
    "function": {
        "name": "web_search",
        "description": "Search the web...",
        "parameters": { ... }
    }
}
```

### ToolResult

Returned from tool execution:
- `content` -- the result text
- `is_error` -- whether execution failed
- `metadata` -- optional dict (e.g. search engine used)

### ToolCallRecord

Persistent record of a single tool invocation:
- `tool_name` -- which tool was called
- `arguments` -- JSON string of arguments
- `result` -- the result text
- `is_error` -- error flag
- `latency_ms` -- execution time

Serialised to JSON and stored in `messages.tool_calls_json`.

### ToolContext

Execution context passed to tool handlers:
- `caller_entity_id` -- who is calling the tool
- `discussion_id` -- which discussion
- `access_mode` -- `private`, `shared`, or `moderator_only`

---

## ToolProvider (Abstract Base Class)

All tool providers implement:

```python
class ToolProvider(ABC):
    name: str

    async def list_tools(self) -> list[ToolDefinition]: ...
    async def execute(self, tool_name, arguments, context,
                      progress_callback=None) -> ToolResult: ...
    async def close(self): ...  # optional cleanup
```

The `progress_callback` parameter (added for MCP progress support) has the
signature `callback(progress: int, total: int, message: str)`. It is used by
`MCPToolProvider` to forward progress notifications from MCP servers. Python
tool providers can ignore it.

### PythonToolProvider

Wraps in-process Python callables (sync or async):

```python
provider = PythonToolProvider("my_tools")
provider.register_tool(
    handler=my_handler_fn,
    definition=ToolDefinition(name="my_tool", ...)
)
```

Handlers receive `(arguments: dict, context: ToolContext)` and return either a
`ToolResult` or a plain string (auto-wrapped).

### MCPToolProvider

Communicates with external MCP servers via JSON-RPC 2.0 over stdin/stdout
subprocesses. Defined in `mcp_client.py`.

```python
provider = MCPToolProvider(
    name="my_mcp_server",
    command="python",
    args=["-m", "my_mcp_server"],
    env={"API_KEY": "..."}
)
await provider.connect()  # launches subprocess, performs MCP handshake
tools = await provider.list_tools()  # calls tools/list
result = await provider.execute("my_tool", {"arg": "val"}, context, progress_callback)
await provider.close()  # terminates subprocess
```

**Key design:**
- Subprocess lifecycle: `connect()` launches the process, performs an
  `initialize` handshake (MCP protocol version `2024-11-05`), and starts a
  background read loop for incoming JSON-RPC messages
- Request/response correlation via sequential integer IDs
- Progress notifications tied to requests via `progressToken` in `_meta`
- Default timeout: 30 seconds per request (configurable)
- `close()` terminates the subprocess and cleans up resources

For full details, see [MCP Expert Plugins](13-mcp-expert-plugins.md).

---

## ToolRegistry

Central aggregation point with access control:

```python
registry = ToolRegistry(db)
registry.register_provider(provider)
```

### Key methods

| Method | Purpose |
|--------|---------|
| `register_provider(provider)` | Add a tool provider |
| `list_all_tools()` | Return all tools from all providers |
| `get_tools_for_entity(entity_id, discussion_id, moderator_id)` | Get tools available to an entity (checks assignments, overrides, shared tools) |
| `execute(tool_name, arguments, caller_entity_id, discussion_id, moderator_id)` | Execute a tool with access control and timeout |

### Access control model

Tools are assigned to entities with one of three access modes:

| Mode | Behaviour |
|------|-----------|
| `private` | Only the assigned entity can use the tool |
| `shared` | All entities in the discussion can use the tool |
| `moderator_only` | Only the moderator can use the tool |

Access is checked during `get_tools_for_entity()`:
1. Entity's own assigned tools (enabled)
2. Shared tools from other entities in the discussion
3. Discussion-level overrides (can disable a tool for a specific discussion)
4. Moderator-only tools restricted to moderator entity

### Safety limits

- **Execution timeout:** 30 seconds per tool call (`TOOL_EXECUTION_TIMEOUT`)
- **Iteration cap:** Maximum 5 tool call rounds per turn (`MAX_TOOL_ITERATIONS`)
- On the final iteration, tool definitions are removed from the API call to
  force the LLM to produce a text response

---

## Built-in Web Search Tool

Defined in `tools_builtin.py`. Created via `create_web_search_provider()`.

### Search engines

1. **Brave Search** (primary) -- requires `BRAVE_SEARCH_API_KEY` environment
   variable. Uses the Brave Search API with `X-Subscription-Token` header.
2. **DuckDuckGo** (fallback) -- no API key required. Parses DuckDuckGo HTML
   search results.

### Schema

```json
{
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "The search query"
        },
        "num_results": {
            "type": "integer",
            "description": "Number of results (1-10)",
            "default": 5
        }
    },
    "required": ["query"]
}
```

### How it works

1. `ConsensusApp._init_builtin_tools()` calls `create_web_search_provider()`
2. The provider is registered with the `ToolRegistry`
3. Users assign the `web_search` tool to entities via the Profiles tab
4. During `generate_turn()`, the LLM can call `web_search` to look up
   information
5. Results are formatted and fed back into the context
6. The LLM produces its final response incorporating the search results

---

## Tool Execution Flow

During `Moderator.generate_turn(entity)`:

```
1. Get available tools for entity
   registry.get_tools_for_entity(entity_id, discussion_id, moderator_id)
       |
       v
2. Convert to OpenAI tool schemas
   [tool.to_openai_schema() for tool in tools]
       |
       v
3. Call LLM with tools
   client.complete_with_tools(messages, model, tools=schemas, ...)
       |
       v
4. Check response
   If finish_reason == "tool_calls":
       |
       +-- For each tool_call in response:
       |     |
       |     +-- Parse function name + arguments
       |     +-- registry.execute(name, args, entity_id, ...)
       |     +-- Record ToolCallRecord (name, args, result, latency)
       |     +-- Append tool result message to context
       |
       +-- Loop back to step 3 (up to MAX_TOOL_ITERATIONS)
       |
   If finish_reason != "tool_calls" or final iteration:
       |
       +-- Extract text content
       +-- Return AIResponse with all tool_calls records
```

---

## Database Tables

Tool data is stored in three tables:
- `tool_providers` -- registered providers (name, type, config)
- `entity_tools` -- tool-to-entity assignments with access mode
- `discussion_tool_overrides` -- per-discussion enable/disable overrides

See [Database](05-database.md) for full schema.

---

## Institutional Memory Tools

Defined in `tools_memory.py`. Created via `create_memory_provider(db)` and
registered in `ConsensusApp._init_memory_tools()`. Requires the `[memory]`
optional dependency group (`sqlite-vec`, `numpy`) and an Ollama embedding
service.

### Architecture

```
ConsensusApp._init_memory_tools()
    |
    +-- create_memory_provider(db)
          |
          +-- EmbeddingClient (async httpx to Ollama /api/embeddings)
          +-- 6 tool handlers (memory_store, memory_recall, memory_forget,
          |                     discussion_search, kg_assert, kg_query)
          +-- PythonToolProvider("memory")
```

### Tools

| Tool | Description | Key Parameters |
|------|-------------|----------------|
| `memory_store` | Store an observation/position to entity's long-term memory | `content: str` |
| `memory_recall` | Semantic search over entity's personal memories | `query: str`, `limit?: int` |
| `memory_forget` | Delete a specific memory by ID | `memory_id: str` |
| `discussion_search` | Semantic search across all past discussion messages | `query: str`, `limit?: int`, `topic_filter?: str` |
| `kg_assert` | Assert a knowledge triple (subject→relation→object) | `subject: str`, `relation: str`, `object: str`, `description?: str` |
| `kg_query` | Query the knowledge graph | `query: str`, `mode: "search"\|"neighbors"\|"path"` |

### How models use memory

Memory tools are **not prompted by the system at invocation time** — they are
presented to the LLM as standard OpenAI function definitions via
`complete_with_tools()`. The LLM autonomously decides when to call them based
on the tool descriptions and conversation context.

However, the **default prompt templates** actively encourage memory use. The
AI Participant system prompt instructs models to:
- Use `memory_recall` and `discussion_search` before responding
- Use `memory_store` to save key insights after contributing
- Use `kg_assert` to record conceptual relationships
- Use `kg_query` to check established knowledge

The AI Moderator system prompt similarly encourages memory use for synthesis
and cross-discussion context.

### Embedding pipeline

- **Backend:** Ollama HTTP API (`/api/embeddings`)
- **Default model:** `nomic-embed-text-v2-moe:latest`
- **Configurable:** Endpoint and model are stored in the `memory_config` DB
  table and editable via the Settings tab in the UI
- **Embedding storage:** Binary blobs in `entity_memory_embeddings`,
  `message_embeddings`, and `kg_node_embeddings` tables
- **Similarity search:** Cosine similarity over packed float vectors
- **Lazy indexing:** Past discussion messages are embedded on first
  `discussion_search` call via a background `asyncio.create_task()`

### Graceful degradation

If the embedding service is unreachable:
- Tool handlers return `ToolResult(is_error=True)` with an informative message
- The entity's turn continues without memory access
- The discussion never crashes due to memory unavailability

### Entity scoping

`memory_store`, `memory_recall`, and `memory_forget` are automatically scoped
to the calling entity via `ToolContext.caller_entity_id`. An entity can only
access its own memories — no cross-entity memory leakage.

---

## Adding a New Tool

To add a custom tool:

1. Create a handler function:
   ```python
   async def my_handler(arguments: dict, context: ToolContext) -> ToolResult:
       result = do_something(arguments["param"])
       return ToolResult(content=str(result))
   ```

2. Create a provider and register the tool:
   ```python
   provider = PythonToolProvider("my_provider")
   provider.register_tool(
       handler=my_handler,
       definition=ToolDefinition(
           name="my_tool",
           description="Does something useful",
           parameters={"type": "object", "properties": {...}, "required": [...]},
           provider_name="my_provider"
       )
   )
   ```

3. Register the provider in `ConsensusApp._init_builtin_tools()`:
   ```python
   self.tool_registry.register_provider(provider)
   ```

4. Assign the tool to entities via the UI or database

---

[Next: Prompts, Providers, and Security](09-prompts-providers-security.md)
