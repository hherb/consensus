# MCP Expert Plugins Design

**Date:** 2026-03-09
**Status:** Approved

## Goal

Enable Consensus to act as an MCP client, connecting to external MCP servers that host long-running specialist tools (e.g., biomedical fact-checker). Experts are invoked on demand during discussions, deliver a single-turn report with real-time progress feedback, then step back without joining the turn rotation.

## Approach

**Approach C: MCPToolProvider + event-based progress layer.** MCPToolProvider extends the existing ToolProvider ABC, keeping expert tools in the same registry as built-in tools. A lightweight event emitter on ConsensusApp decouples progress reporting from tool execution, with desktop and web modes subscribing independently.

## Architecture

```
Human / AI entity
    ↓ consult_expert(name, query)
ConsensusApp
    ├── ToolRegistry
    │   ├── PythonToolProvider (built-in tools)
    │   └── MCPToolProvider (stdio subprocess)
    │       ├── initialize → tools/list → tools/call
    │       └── notifications/progress → progress_callback
    ├── EventEmitter
    │   └── emit("tool_progress", {discussion_id, entity_name, tool_name, progress, total, message})
    └── Expert entity message added to discussion
            ↓
Desktop: evaluate_js('onToolProgress(...)') / Web: SSE /api/events
            ↓
Frontend: progress indicator with stage text + progress bar
```

## Components

### 1. MCPToolProvider (`consensus/mcp_client.py`)

New class extending `ToolProvider`:

- **Lifecycle:** Launches MCP server subprocess via `asyncio.create_subprocess_exec()`. Communicates over stdin/stdout using MCP JSON-RPC protocol. Sends `initialize` on startup, `tools/list` to discover tools, `tools/call` to execute.
- **Tool discovery:** `list_tools()` calls MCP `tools/list` and converts responses into `ToolDefinition` objects (MCP tool schema is nearly identical to OpenAI function schema).
- **Execution:** `execute(tool_name, arguments, context, progress_callback)` sends `tools/call` with a `progressToken` in `_meta`. While awaiting the result, reads JSON-RPC messages from stdout — forwarding any `notifications/progress` to the `progress_callback`. Returns the final result as a `ToolResult`.
- **Connection management:** `close()` sends MCP `shutdown`, then terminates the subprocess. Auto-restart on unexpected exit with backoff.
- **Registration:** MCPToolProvider instances register in the existing ToolRegistry like any other provider.

The `ToolProvider.execute()` interface gains an optional `progress_callback` parameter. Built-in providers ignore it; MCPToolProvider uses it.

### 2. Event Layer

Lightweight event emitter on `ConsensusApp`:

- **`app.emit(event_type, data)`** — fires an event. Data always includes `discussion_id` for scoping.
- **Initial event type:** `"tool_progress"` — `{discussion_id, entity_name, tool_name, progress, total, message}`.
- **Desktop mode:** `DesktopBridge` subscribes and calls `window.evaluate_js()` to invoke `onToolProgress(data)`.
- **Web mode:** New SSE endpoint `GET /api/events`. Frontend opens an `EventSource` connection. Events stream as they're emitted. Reconnects automatically (native EventSource behavior).
- **Multi-user scoping:** Events are emitted on the `ConsensusApp` instance. In multi-user mode each session has its own instance, so events are inherently scoped.

Implementation: dict of `event_type → [callbacks]`, ~20 lines. No external dependency.

### 3. Expert Entity & Invocation Flow

**Entity type:**
- New `entity_type` field on `Entity` model: `"ai"` (existing default), `"human"` (existing), `"expert"` (new).
- Expert entities are linked to an MCP server + specific tool. Not added to discussion members by default — available globally, invoked on demand.

**Invocation by AI entities (tool call):**
- Meta-tool `consult_expert(expert_name: str, query: str)` registered in tool registry, available to all AI participants.
- Execution flow:
  1. Resolve expert entity → MCP server + tool
  2. Call `mcp_provider.execute(tool_name, arguments, progress_callback=...)`
  3. Progress callback calls `app.emit("tool_progress", ...)`
  4. Final result returned as `ToolResult` to calling entity's context
  5. Separate message added to discussion from expert entity (the expert's "turn")

**Invocation by humans:**
- "Consult Expert" button in discussion UI (next to message input).
- Dropdown of available experts (with descriptions), then text field for query.
- Calls `POST /api/consult_expert`, follows same backend flow.
- Expert report appears as new message in discussion thread.

**One-turn semantics:**
- Expert never joins turn rotation. After delivering report, does not participate again unless re-invoked.
- Expert message stored in database like any other message, with expert's entity_id as sender.

### 4. Frontend Progress Display

**Progress indicator:**
```
Medical Fact-Checker: Scoring documents... (5/20)
▓▓▓▓▓▓░░░░░░░░░░░░░░ 25%
```

- Stage text from MCP `notifications/progress` message field.
- Progress bar uses `progress/total`. If total unknown, show stage text with spinner only.
- Each notification replaces previous (update in-place).

**JS event handling:**
- Desktop: `DesktopBridge` calls `window.evaluate_js('onToolProgress(...)')`.
- Web: `EventSource` to `/api/events`.
- `onToolProgress(data)` updates typing indicator DOM for the given entity.

**After completion:**
- Normal state update fires (`onStateUpdate`), rendering expert message in thread.
- Progress indicator clears automatically.

**Expert message rendering:**
- Distinct visual treatment — badge/icon indicating "Expert Consultation" with expert name.
- Full report as message content, rendered as markdown.

### 5. Database & Configuration

**New table `mcp_servers`:**

| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER PRIMARY KEY | |
| name | TEXT UNIQUE | Display name |
| description | TEXT | Human-readable description of the service |
| command | TEXT | Executable to launch |
| args | TEXT | JSON array of additional arguments |
| env | TEXT | JSON object of environment variables |
| enabled | INTEGER DEFAULT 1 | |
| created_at | TIMESTAMP | |
| updated_at | TIMESTAMP | |

**New table `expert_definitions`:**

| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER PRIMARY KEY | |
| entity_id | INTEGER REFERENCES entities(id) | The expert entity |
| mcp_server_id | INTEGER REFERENCES mcp_servers(id) | |
| tool_name | TEXT | Which tool on the server |
| description | TEXT | What this expert does |
| default_arguments | TEXT | JSON object of default/fixed args |
| timeout_seconds | INTEGER DEFAULT 300 | Per-expert timeout |

**Schema changes:**
- `entities` table gains `entity_type` TEXT DEFAULT 'ai'. Existing rows default to 'ai'.

**Migration:** New SQL migration file in `consensus/migrations/`.

**UI in Providers tab:**
- "MCP Servers" section: list, add, edit, delete, enable/disable toggle.
- "Test Connection" button: launches server, sends `initialize` + `tools/list`, displays discovered tools, shuts down.
- Profiles tab: when `entity_type` is "expert", dropdown to select MCP server and tool.

### 6. Error Handling & Timeouts

**MCP server lifecycle errors:**
- Subprocess fails to start → log error, mark server unavailable. `consult_expert` returns `ToolResult(is_error=True)` with clear message. No crash, no retry loop.
- Subprocess dies mid-execution → pending `execute()` gets error result. Progress indicator clears, expert message shows error.

**Timeouts:**
- Expert tools: configurable per expert via `expert_definitions.timeout_seconds` (default 300s).
- Non-expert MCP tools: standard 30s timeout.
- On timeout: cancel awaiting coroutine, return error result. Subprocess not killed (may be shared).

**Progress stall detection:**
- No progress notification for 60s → show "Still working..." in indicator (UI-only, no backend action).

**Graceful degradation:**
- SSE connection drops → progress updates lost, but final result still arrives via normal state update. Expert message appears correctly; user misses intermediate progress only.

## Out of Scope (Future)

- MCP Streamable HTTP transport
- Multiple simultaneous expert consultations
- Expert-to-expert chaining
- Config file-based MCP server definitions
- MCP resources and prompts (only tools for now)

These items are tracked in ROADMAP.md.
