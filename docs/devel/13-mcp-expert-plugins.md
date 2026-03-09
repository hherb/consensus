# 13. MCP Expert Plugins

[Back to index](programmer-manual.md) | [Previous: Cost Tracking](12-cost-tracking.md)

---

The MCP expert plugins system allows Consensus to integrate with external tools
via the Model Context Protocol (MCP). MCP servers run as subprocesses and
communicate via JSON-RPC 2.0 over stdin/stdout. Expert entities wrap MCP tools
as consultable discussion participants.

## Overview

```
ConsensusApp
    |
    +-- _mcp_providers: {server_id: MCPToolProvider}
    |
    +-- ToolRegistry
    |     +-- PythonToolProvider ("builtin")
    |           +-- consult_expert  ← meta-tool registered at init
    |
    +-- Database
          +-- mcp_servers table
          +-- expert_definitions table
          +-- entities table (entity_type = 'expert')

Flow during turn generation:
    AI entity calls consult_expert(expert_name, query)
        → ConsensusApp._handle_consult_expert()
            → Finds expert entity + expert_definition
            → Connects to MCP server if not already connected
            → MCPToolProvider.execute(tool_name, arguments)
            → Expert response added as message to discussion
            → tool_progress events emitted via SSE
```

## MCPToolProvider

Defined in `mcp_client.py`. Implements the `ToolProvider` ABC for
communication with MCP server subprocesses.

### Construction

```python
provider = MCPToolProvider(
    name="my_server",
    command="python",
    args=["-m", "my_mcp_server"],
    env={"API_KEY": "secret"}
)
```

### Lifecycle

1. **`connect()`** — launches the subprocess, performs the MCP `initialize`
   handshake (protocol version `2024-11-05`), sends `initialized`
   notification, starts background read loop
2. **`list_tools()`** — sends `tools/list` JSON-RPC request, returns
   `ToolDefinition` objects
3. **`execute(tool_name, arguments, context, progress_callback)`** — sends
   `tools/call` request with optional `progressToken` in `_meta`, returns
   `ToolResult`
4. **`close()`** — terminates subprocess, cancels read loop, cleans up

### JSON-RPC 2.0 protocol

All communication uses newline-delimited JSON over stdin/stdout:

**Request:**
```json
{"jsonrpc": "2.0", "id": 1, "method": "tools/call",
 "params": {"name": "my_tool", "arguments": {"query": "test"},
            "_meta": {"progressToken": "req-1"}}}
```

**Response:**
```json
{"jsonrpc": "2.0", "id": 1,
 "result": {"content": [{"type": "text", "text": "Result here"}]}}
```

**Progress notification (server → client):**
```json
{"jsonrpc": "2.0", "method": "notifications/progress",
 "params": {"progressToken": "req-1", "progress": 50, "total": 100}}
```

### Internal mechanics

- **Read loop:** `_read_loop()` runs as an `asyncio.Task`, reading
  newline-delimited JSON from stdout and dispatching via `_handle_message()`
- **Response correlation:** Pending requests stored in
  `_pending: dict[int, asyncio.Future]`, keyed by request ID
- **Progress routing:** Progress callbacks stored in
  `_progress_callbacks: dict[str, Callable]`, keyed by progress token
- **Timeout:** Requests default to 30 seconds (`DEFAULT_TIMEOUT`), raised as
  `asyncio.TimeoutError`

---

## Expert Entities

Expert entities are a new entity type (`entity_type = 'expert'`) that wrap an
MCP tool as a consultable participant in discussions.

### Data model

**`mcp_servers` table:**
| Column | Type | Purpose |
|--------|------|---------|
| `id` | INTEGER PK | Auto-increment ID |
| `name` | TEXT UNIQUE | Display name |
| `description` | TEXT | Human-readable description |
| `command` | TEXT | Executable path (e.g. `python`, `node`) |
| `args` | TEXT (JSON) | Command-line arguments array |
| `env` | TEXT (JSON) | Environment variables object |
| `enabled` | INTEGER | Whether the server is active |

**`expert_definitions` table:**
| Column | Type | Purpose |
|--------|------|---------|
| `entity_id` | INTEGER UNIQUE FK | Links to `entities.id` |
| `mcp_server_id` | INTEGER FK | Links to `mcp_servers.id` |
| `tool_name` | TEXT | Which MCP tool to call |
| `description` | TEXT | Expert's speciality description |
| `default_arguments` | TEXT (JSON) | Default args merged with query |
| `query_param_name` | TEXT | Parameter name for the query (default: `query`) |
| `timeout_seconds` | INTEGER | Per-expert timeout (default: 30) |

### Creating an expert

1. Register an MCP server via the UI or API (`add_mcp_server`)
2. Test the connection (`test_mcp_connection`) to discover available tools
3. Create an entity with `entity_type = 'expert'`
4. Link the entity to an MCP tool via `save_expert_definition`

---

## The `consult_expert` Meta-Tool

Registered as a built-in tool in `ConsensusApp._init_builtin_tools()`. This
is the tool that AI participants call to consult expert entities during turn
generation.

### Schema

```json
{
    "type": "object",
    "properties": {
        "expert_name": {
            "type": "string",
            "description": "Name of the expert entity to consult"
        },
        "query": {
            "type": "string",
            "description": "The question or request for the expert"
        }
    },
    "required": ["expert_name", "query"]
}
```

### Execution flow (`_handle_consult_expert`)

1. Looks up the expert entity by name
2. Retrieves the expert's `expert_definition` from the database
3. Gets or creates an `MCPToolProvider` connection for the MCP server
4. Builds tool arguments: merges `default_arguments` with
   `{query_param_name: query}`
5. Calls `MCPToolProvider.execute()` with a progress callback
6. Emits `tool_progress` events (forwarded to SSE clients)
7. Adds the expert's response as a message in the discussion
   (`role = "participant"`, `entity_id` = expert's entity ID)
8. Returns the response text as a `ToolResult`

---

## Event Emitter

`ConsensusApp` includes a lightweight event emitter for real-time
notifications:

```python
# Register a handler
app.on("tool_progress", handler_fn)

# Emit an event
app.emit("tool_progress", {"progress": 50, "total": 100, "message": "..."})
```

Used by:
- `_handle_consult_expert()` — emits `tool_progress` during MCP tool execution
- `server.py` SSE endpoint — listens for `tool_progress` and forwards to
  connected browsers
- `desktop.py` — forwards progress events via `evaluate_js`

---

## SSE Progress Endpoint

`GET /api/events` provides a Server-Sent Events stream:

```
event: tool_progress
data: {"progress": 50, "total": 100, "message": "Consulting PubMed expert..."}

: keepalive
```

Implementation in `server.py`:
1. Creates an `asyncio.Queue` per connected client
2. Registers a `tool_progress` event handler that pushes to all queues
3. Sends keepalive comments every 30 seconds
4. Cleans up queue and handler on client disconnect

The frontend connects to this endpoint and displays a progress indicator
during expert consultations.

---

## Frontend UI

The frontend adds several UI elements for MCP expert management:

- **MCP Servers tab** — manage registered MCP server configurations (add,
  edit, test connection, delete)
- **Expert entity rendering** — expert entities are visually distinguished
  in the participant sidebar and message feed
- **Progress indicator** — displays real-time progress during expert
  consultations (connected via SSE)
- **Expert messages** — expert responses appear as regular discussion
  messages with expert-specific styling

---

## Tests

Tests are in `tests/test_mcp_client.py` and `tests/test_mcp_integration.py`:

- **Unit tests:** Mock subprocess communication, test JSON-RPC message
  handling, connect/list_tools/execute/close lifecycle
- **Integration tests:** Full MCP stdio JSON-RPC flow with a mock MCP server
  subprocess, testing the complete request-response cycle

---

[Back to index](programmer-manual.md)
