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
    |     |
    |     +-- (future: MCPToolProvider, etc.)
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
    async def execute(self, tool_name, arguments, context) -> ToolResult: ...
    async def close(self): ...  # optional cleanup
```

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
