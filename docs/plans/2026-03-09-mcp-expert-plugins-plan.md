# MCP Expert Plugins Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Enable Consensus to act as an MCP client, connecting to external MCP servers via stdio to invoke long-running expert tools with real-time progress feedback in the UI.

**Architecture:** MCPToolProvider extends the existing ToolProvider ABC, communicating with MCP server subprocesses over JSON-RPC/stdio. A lightweight event emitter on ConsensusApp decouples progress reporting, with SSE (web) and evaluate_js (desktop) delivering updates. Expert entities are invoked on demand via a `consult_expert` meta-tool or human UI button.

**Tech Stack:** Python asyncio (subprocess management, JSON-RPC), aiohttp SSE (web mode), pywebview evaluate_js (desktop mode), SQLite (persistence).

**Design doc:** `docs/plans/2026-03-09-mcp-expert-plugins-design.md`

---

### Task 1: Database Migration — MCP Server & Expert Tables

**Files:**
- Create: `consensus/migrations/004_mcp_experts.sql`
- Modify: `consensus/models.py` (EntityType enum)
- Test: `tests/test_database.py`

**Step 1: Write the migration SQL**

Create `consensus/migrations/004_mcp_experts.sql`:

```sql
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

-- Add entity_type column to entities table
ALTER TABLE entities ADD COLUMN entity_type TEXT NOT NULL DEFAULT 'ai';
```

**Step 2: Write tests for the migration**

Add to `tests/test_database.py`:

```python
class TestMCPServerCRUD:
    def test_add_mcp_server(self, tmp_db):
        server_id = tmp_db.add_mcp_server(
            name="BM Librarian",
            description="Biomedical literature search",
            command="uvx",
            args=["bmlibrarian-mcp"],
            env={"BRAVE_API_KEY": "test"},
        )
        assert server_id is not None
        server = tmp_db.get_mcp_server(server_id)
        assert server["name"] == "BM Librarian"
        assert server["description"] == "Biomedical literature search"
        assert server["enabled"] == 1

    def test_get_mcp_servers(self, tmp_db):
        tmp_db.add_mcp_server("Server1", "Desc1", "cmd1")
        tmp_db.add_mcp_server("Server2", "Desc2", "cmd2")
        servers = tmp_db.get_mcp_servers()
        assert len(servers) == 2

    def test_update_mcp_server(self, tmp_db):
        sid = tmp_db.add_mcp_server("Old", "Desc", "cmd")
        tmp_db.update_mcp_server(sid, name="New", enabled=0)
        server = tmp_db.get_mcp_server(sid)
        assert server["name"] == "New"
        assert server["enabled"] == 0

    def test_delete_mcp_server(self, tmp_db):
        sid = tmp_db.add_mcp_server("ToDelete", "Desc", "cmd")
        tmp_db.delete_mcp_server(sid)
        assert tmp_db.get_mcp_server(sid) is None

    def test_add_expert_definition(self, tmp_db, sample_ai_entity):
        sid = tmp_db.add_mcp_server("Server", "Desc", "cmd")
        eid = tmp_db.add_expert_definition(
            entity_id=sample_ai_entity,
            mcp_server_id=sid,
            tool_name="fact_check_claim",
            description="Medical fact-checker",
            default_arguments={"search_provider": "both"},
            timeout_seconds=300,
        )
        assert eid is not None
        defn = tmp_db.get_expert_definition(sample_ai_entity)
        assert defn["tool_name"] == "fact_check_claim"
        assert defn["timeout_seconds"] == 300

    def test_get_expert_definitions(self, tmp_db, sample_ai_entity):
        sid = tmp_db.add_mcp_server("Server", "Desc", "cmd")
        tmp_db.add_expert_definition(sample_ai_entity, sid, "tool1", "Expert 1")
        experts = tmp_db.get_expert_definitions()
        assert len(experts) >= 1
```

**Step 3: Run tests to verify they fail**

Run: `python -m pytest tests/test_database.py::TestMCPServerCRUD -v`
Expected: FAIL — methods don't exist yet.

**Step 4: Implement database methods**

Add to `consensus/database.py` the following methods:

```python
# --- MCP Server CRUD ---

def add_mcp_server(self, name: str, description: str, command: str,
                   args: list | None = None, env: dict | None = None) -> int:
    now = time.time()
    cur = self._execute_write(
        "INSERT INTO mcp_servers (name, description, command, args, env, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (name, description, command, json.dumps(args or []), json.dumps(env or {}), now, now),
    )
    return cur.lastrowid

def get_mcp_server(self, server_id: int) -> dict | None:
    row = self.conn.execute("SELECT * FROM mcp_servers WHERE id = ?", (server_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["args"] = json.loads(d["args"])
    d["env"] = json.loads(d["env"])
    return d

def get_mcp_servers(self, enabled_only: bool = False) -> list[dict]:
    sql = "SELECT * FROM mcp_servers"
    if enabled_only:
        sql += " WHERE enabled = 1"
    rows = self.conn.execute(sql).fetchall()
    result = []
    for row in rows:
        d = dict(row)
        d["args"] = json.loads(d["args"])
        d["env"] = json.loads(d["env"])
        result.append(d)
    return result

def update_mcp_server(self, server_id: int, **kwargs) -> bool:
    allowed = {"name", "description", "command", "args", "env", "enabled"}
    if "args" in kwargs:
        kwargs["args"] = json.dumps(kwargs["args"])
    if "env" in kwargs:
        kwargs["env"] = json.dumps(kwargs["env"])
    self._update_row("mcp_servers", server_id, allowed, extra_sets={"updated_at": time.time()}, **kwargs)
    return True

def delete_mcp_server(self, server_id: int) -> bool:
    self._execute_write("DELETE FROM expert_definitions WHERE mcp_server_id = ?", (server_id,))
    self._execute_write("DELETE FROM mcp_servers WHERE id = ?", (server_id,))
    return True

# --- Expert Definition CRUD ---

def add_expert_definition(self, entity_id: int, mcp_server_id: int, tool_name: str,
                          description: str = "", default_arguments: dict | None = None,
                          timeout_seconds: int = 300) -> int:
    cur = self._execute_write(
        "INSERT INTO expert_definitions (entity_id, mcp_server_id, tool_name, description, "
        "default_arguments, timeout_seconds) VALUES (?, ?, ?, ?, ?, ?)",
        (entity_id, mcp_server_id, tool_name, description,
         json.dumps(default_arguments or {}), timeout_seconds),
    )
    return cur.lastrowid

def get_expert_definition(self, entity_id: int) -> dict | None:
    row = self.conn.execute(
        "SELECT ed.*, ms.name AS server_name, ms.command, ms.args AS server_args, "
        "ms.env AS server_env, ms.enabled AS server_enabled "
        "FROM expert_definitions ed JOIN mcp_servers ms ON ed.mcp_server_id = ms.id "
        "WHERE ed.entity_id = ?", (entity_id,),
    ).fetchone()
    if not row:
        return None
    d = dict(row)
    d["default_arguments"] = json.loads(d["default_arguments"])
    d["server_args"] = json.loads(d["server_args"])
    d["server_env"] = json.loads(d["server_env"])
    return d

def get_expert_definitions(self) -> list[dict]:
    rows = self.conn.execute(
        "SELECT ed.*, ms.name AS server_name, ms.command "
        "FROM expert_definitions ed JOIN mcp_servers ms ON ed.mcp_server_id = ms.id"
    ).fetchall()
    result = []
    for row in rows:
        d = dict(row)
        d["default_arguments"] = json.loads(d["default_arguments"])
        result.append(d)
    return result
```

Also update `EntityType` enum in `consensus/models.py`:

```python
class EntityType(Enum):
    HUMAN = "human"
    AI = "ai"
    EXPERT = "expert"
```

**Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_database.py::TestMCPServerCRUD -v`
Expected: PASS

**Step 6: Commit**

```bash
git add consensus/migrations/004_mcp_experts.sql consensus/database.py consensus/models.py tests/test_database.py
git commit -m "feat: add MCP server and expert definition tables with CRUD"
```

---

### Task 2: Event Emitter on ConsensusApp

**Files:**
- Modify: `consensus/app.py` (~lines 36-130)
- Test: `tests/test_app.py`

**Step 1: Write failing tests**

Add to `tests/test_app.py`:

```python
class TestEventEmitter:
    def test_emit_calls_subscriber(self, tmp_db):
        app = ConsensusApp(db_path=str(tmp_db.db_path))
        received = []
        app.on("tool_progress", lambda data: received.append(data))
        app.emit("tool_progress", {"message": "Searching..."})
        assert len(received) == 1
        assert received[0]["message"] == "Searching..."

    def test_multiple_subscribers(self, tmp_db):
        app = ConsensusApp(db_path=str(tmp_db.db_path))
        a, b = [], []
        app.on("tool_progress", lambda d: a.append(d))
        app.on("tool_progress", lambda d: b.append(d))
        app.emit("tool_progress", {"x": 1})
        assert len(a) == 1 and len(b) == 1

    def test_off_removes_subscriber(self, tmp_db):
        app = ConsensusApp(db_path=str(tmp_db.db_path))
        received = []
        cb = lambda d: received.append(d)
        app.on("tool_progress", cb)
        app.off("tool_progress", cb)
        app.emit("tool_progress", {"x": 1})
        assert len(received) == 0

    def test_emit_unknown_event_no_error(self, tmp_db):
        app = ConsensusApp(db_path=str(tmp_db.db_path))
        app.emit("nonexistent", {})  # should not raise
```

**Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_app.py::TestEventEmitter -v`
Expected: FAIL — `on`, `off`, `emit` methods don't exist.

**Step 3: Implement event emitter**

Add to `ConsensusApp.__init__()` in `consensus/app.py`:

```python
self._event_listeners: dict[str, list[Callable]] = {}
```

Add methods to `ConsensusApp`:

```python
def on(self, event_type: str, callback: Callable) -> None:
    """Subscribe to an event type."""
    self._event_listeners.setdefault(event_type, []).append(callback)

def off(self, event_type: str, callback: Callable) -> None:
    """Unsubscribe from an event type."""
    listeners = self._event_listeners.get(event_type, [])
    if callback in listeners:
        listeners.remove(callback)

def emit(self, event_type: str, data: dict) -> None:
    """Emit an event to all subscribers."""
    for cb in self._event_listeners.get(event_type, []):
        try:
            cb(data)
        except Exception:
            logger.exception("Event listener error for %s", event_type)
```

**Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_app.py::TestEventEmitter -v`
Expected: PASS

**Step 5: Commit**

```bash
git add consensus/app.py tests/test_app.py
git commit -m "feat: add event emitter to ConsensusApp for real-time notifications"
```

---

### Task 3: MCPToolProvider — Core MCP Client

**Files:**
- Create: `consensus/mcp_client.py`
- Test: `tests/test_mcp_client.py`

**Step 1: Write failing tests**

Create `tests/test_mcp_client.py`. Since we need a real MCP server subprocess for integration tests, we'll test with a mock subprocess approach:

```python
"""Tests for consensus.mcp_client — MCP stdio client."""

import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from consensus.mcp_client import MCPToolProvider
from consensus.tools import ToolContext, ToolResult


class TestMCPToolProviderInit:
    @pytest.mark.asyncio
    async def test_create_provider(self):
        provider = MCPToolProvider(
            name="test-server",
            command="echo",
            args=["hello"],
        )
        assert provider.name == "test-server"
        assert provider._command == "echo"
        assert provider._args == ["hello"]
        await provider.close()


class TestMCPJsonRpc:
    """Test JSON-RPC message framing."""

    def test_encode_request(self):
        from consensus.mcp_client import encode_jsonrpc_request
        msg = encode_jsonrpc_request("tools/list", {}, req_id=1)
        parsed = json.loads(msg)
        assert parsed["jsonrpc"] == "2.0"
        assert parsed["method"] == "tools/list"
        assert parsed["id"] == 1

    def test_encode_request_with_meta(self):
        from consensus.mcp_client import encode_jsonrpc_request
        msg = encode_jsonrpc_request(
            "tools/call",
            {"name": "test", "arguments": {}, "_meta": {"progressToken": "tok1"}},
            req_id=2,
        )
        parsed = json.loads(msg)
        assert parsed["params"]["_meta"]["progressToken"] == "tok1"


class TestMCPToolDiscovery:
    @pytest.mark.asyncio
    async def test_list_tools_parses_response(self):
        """Test that list_tools converts MCP tool schemas to ToolDefinitions."""
        provider = MCPToolProvider(name="test", command="echo", args=[])

        # Mock the _send_request to return MCP tools/list response
        mock_tools_response = {
            "tools": [
                {
                    "name": "fact_check_claim",
                    "description": "Check a medical claim",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "claim": {"type": "string", "description": "The claim"},
                        },
                        "required": ["claim"],
                    },
                }
            ]
        }
        provider._send_request = AsyncMock(return_value=mock_tools_response)
        provider._connected = True

        tools = await provider.list_tools()
        assert len(tools) == 1
        assert tools[0].name == "fact_check_claim"
        assert tools[0].parameters["properties"]["claim"]["type"] == "string"
        await provider.close()


class TestMCPToolExecution:
    @pytest.mark.asyncio
    async def test_execute_returns_result(self):
        provider = MCPToolProvider(name="test", command="echo", args=[])
        provider._connected = True

        mock_result = {"content": [{"type": "text", "text": "Claim is supported."}]}
        provider._send_request = AsyncMock(return_value=mock_result)

        ctx = ToolContext(caller_entity_id=1, discussion_id=1)
        result = await provider.execute("fact_check", {"claim": "test"}, ctx)
        assert result.content == "Claim is supported."
        assert result.is_error is False
        await provider.close()

    @pytest.mark.asyncio
    async def test_execute_with_progress_callback(self):
        provider = MCPToolProvider(name="test", command="echo", args=[])
        provider._connected = True

        progress_events = []

        async def mock_send_with_progress(method, params, timeout=None, progress_callback=None):
            # Simulate progress notifications
            if progress_callback:
                progress_callback(1, 10, "Searching literature...")
                progress_callback(5, 10, "Scoring documents...")
            return {"content": [{"type": "text", "text": "Done"}]}

        provider._send_request = mock_send_with_progress

        ctx = ToolContext(caller_entity_id=1, discussion_id=1)
        cb = lambda p, t, m: progress_events.append((p, t, m))
        result = await provider.execute("tool", {}, ctx, progress_callback=cb)
        assert len(progress_events) == 2
        assert progress_events[0] == (1, 10, "Searching literature...")
        await provider.close()

    @pytest.mark.asyncio
    async def test_execute_error_result(self):
        provider = MCPToolProvider(name="test", command="echo", args=[])
        provider._connected = True

        mock_result = {"content": [{"type": "text", "text": "Error occurred"}], "isError": True}
        provider._send_request = AsyncMock(return_value=mock_result)

        ctx = ToolContext(caller_entity_id=1, discussion_id=1)
        result = await provider.execute("tool", {}, ctx)
        assert result.is_error is True
        await provider.close()

    @pytest.mark.asyncio
    async def test_execute_not_connected(self):
        provider = MCPToolProvider(name="test", command="echo", args=[])
        provider._connected = False

        ctx = ToolContext(caller_entity_id=1, discussion_id=1)
        result = await provider.execute("tool", {}, ctx)
        assert result.is_error is True
        assert "not connected" in result.content.lower()
        await provider.close()
```

**Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_mcp_client.py -v`
Expected: FAIL — module doesn't exist.

**Step 3: Implement MCPToolProvider**

Create `consensus/mcp_client.py`:

```python
"""MCP client — stdio transport for connecting to external MCP servers."""

import asyncio
import json
import logging
from typing import Any, Callable, Optional

from .tools import ToolContext, ToolDefinition, ToolProvider, ToolResult

logger = logging.getLogger(__name__)

_next_id = 0


def _get_id() -> int:
    global _next_id
    _next_id += 1
    return _next_id


def encode_jsonrpc_request(method: str, params: dict, req_id: int) -> str:
    """Encode a JSON-RPC 2.0 request."""
    return json.dumps({
        "jsonrpc": "2.0",
        "id": req_id,
        "method": method,
        "params": params,
    })


class MCPToolProvider(ToolProvider):
    """MCP client that communicates with an MCP server subprocess over stdio."""

    def __init__(self, name: str, command: str, args: list[str] | None = None,
                 env: dict[str, str] | None = None) -> None:
        super().__init__(name)
        self._command = command
        self._args = args or []
        self._env = env
        self._process: Optional[asyncio.subprocess.Process] = None
        self._connected = False
        self._pending: dict[int, asyncio.Future] = {}
        self._reader_task: Optional[asyncio.Task] = None
        self._progress_callbacks: dict[str, Callable] = {}
        self._tools_cache: list[ToolDefinition] = []

    async def connect(self) -> bool:
        """Launch the MCP server subprocess and perform initialization."""
        try:
            import os
            env = os.environ.copy()
            if self._env:
                env.update(self._env)
            self._process = await asyncio.create_subprocess_exec(
                self._command, *self._args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            self._reader_task = asyncio.create_task(self._read_loop())

            # MCP initialize handshake
            result = await self._send_request("initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "consensus", "version": "1.0.0"},
            })
            if result is None:
                logger.error("MCP initialize failed for %s", self.name)
                return False

            # Send initialized notification (no id = notification)
            await self._send_notification("notifications/initialized", {})
            self._connected = True
            logger.info("MCP server %s connected", self.name)
            return True
        except Exception:
            logger.exception("Failed to connect to MCP server %s", self.name)
            return False

    async def _read_loop(self) -> None:
        """Read JSON-RPC messages from the subprocess stdout."""
        assert self._process and self._process.stdout
        while True:
            try:
                line = await self._process.stdout.readline()
                if not line:
                    break
                msg = json.loads(line.decode().strip())
                self._handle_message(msg)
            except json.JSONDecodeError:
                continue
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Error in MCP read loop for %s", self.name)
                break
        self._connected = False

    def _handle_message(self, msg: dict) -> None:
        """Route incoming JSON-RPC messages."""
        if "id" in msg and "result" in msg:
            # Response to a request
            req_id = msg["id"]
            if req_id in self._pending:
                self._pending[req_id].set_result(msg.get("result"))
        elif "id" in msg and "error" in msg:
            req_id = msg["id"]
            if req_id in self._pending:
                self._pending[req_id].set_exception(
                    RuntimeError(f"MCP error: {msg['error']}")
                )
        elif msg.get("method") == "notifications/progress":
            # Progress notification
            params = msg.get("params", {})
            token = params.get("progressToken")
            if token and token in self._progress_callbacks:
                self._progress_callbacks[token](
                    params.get("progress", 0),
                    params.get("total"),
                    params.get("message", ""),
                )

    async def _send_request(self, method: str, params: dict,
                            timeout: float | None = None,
                            progress_callback: Callable | None = None) -> Any:
        """Send a JSON-RPC request and await the response."""
        assert self._process and self._process.stdin
        req_id = _get_id()
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[req_id] = future

        progress_token = None
        if progress_callback:
            progress_token = f"progress-{req_id}"
            self._progress_callbacks[progress_token] = progress_callback
            params.setdefault("_meta", {})["progressToken"] = progress_token

        encoded = encode_jsonrpc_request(method, params, req_id)
        self._process.stdin.write((encoded + "\n").encode())
        await self._process.stdin.drain()

        try:
            if timeout:
                return await asyncio.wait_for(future, timeout=timeout)
            return await future
        finally:
            self._pending.pop(req_id, None)
            if progress_token:
                self._progress_callbacks.pop(progress_token, None)

    async def _send_notification(self, method: str, params: dict) -> None:
        """Send a JSON-RPC notification (no id, no response expected)."""
        assert self._process and self._process.stdin
        msg = json.dumps({"jsonrpc": "2.0", "method": method, "params": params})
        self._process.stdin.write((msg + "\n").encode())
        await self._process.stdin.drain()

    async def list_tools(self) -> list[ToolDefinition]:
        """Discover tools from the MCP server."""
        if not self._connected:
            return []
        result = await self._send_request("tools/list", {})
        tools = []
        for t in result.get("tools", []):
            td = ToolDefinition(
                name=t["name"],
                description=t.get("description", ""),
                parameters=t.get("inputSchema", {"type": "object", "properties": {}}),
                provider_name=self.name,
            )
            tools.append(td)
        self._tools_cache = tools
        return tools

    async def execute(self, tool_name: str, arguments: dict, context: ToolContext,
                      progress_callback: Callable | None = None) -> ToolResult:
        """Execute a tool on the MCP server."""
        if not self._connected:
            return ToolResult(content=f"MCP server {self.name} is not connected", is_error=True)

        try:
            params = {"name": tool_name, "arguments": arguments}
            result = await self._send_request(
                "tools/call", params,
                progress_callback=progress_callback,
            )
            # MCP returns content as array of content blocks
            content_blocks = result.get("content", [])
            text_parts = [b["text"] for b in content_blocks if b.get("type") == "text"]
            content = "\n".join(text_parts) if text_parts else str(result)
            is_error = result.get("isError", False)
            return ToolResult(content=content, is_error=is_error)
        except asyncio.TimeoutError:
            return ToolResult(content=f"Tool {tool_name} timed out", is_error=True)
        except Exception as e:
            logger.exception("MCP tool execution failed: %s", tool_name)
            return ToolResult(content=f"Tool execution failed: {e}", is_error=True)

    async def close(self) -> None:
        """Shut down the MCP server subprocess."""
        self._connected = False
        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
        if self._process:
            try:
                self._process.stdin.close() if self._process.stdin else None
                self._process.terminate()
                await asyncio.wait_for(self._process.wait(), timeout=5)
            except Exception:
                self._process.kill()
            self._process = None
```

**Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_mcp_client.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add consensus/mcp_client.py tests/test_mcp_client.py
git commit -m "feat: add MCPToolProvider for stdio MCP server communication"
```

---

### Task 4: Update ToolProvider Interface for Progress Callbacks

**Files:**
- Modify: `consensus/tools.py` (~lines 91-108, 131-149, 234-300)
- Test: `tests/test_tools.py`

**Step 1: Write failing test**

Add to `tests/test_tools.py`:

```python
class TestProgressCallback:
    @pytest.mark.asyncio
    async def test_python_provider_ignores_progress_callback(self):
        """PythonToolProvider should accept and ignore progress_callback."""
        p = PythonToolProvider("test")
        td = ToolDefinition(name="add", description="Add", parameters={"type": "object", "properties": {}})
        p.register(td, lambda args, ctx: ToolResult(content="ok"))
        ctx = ToolContext(caller_entity_id=1, discussion_id=1)
        result = await p.execute("add", {}, ctx, progress_callback=lambda p, t, m: None)
        assert result.content == "ok"

    @pytest.mark.asyncio
    async def test_registry_execute_passes_progress_callback(self):
        """ToolRegistry.execute should forward progress_callback to provider."""
        p = PythonToolProvider("test")
        td = ToolDefinition(name="echo", description="Echo", parameters={"type": "object", "properties": {}})
        received = []
        p.register(td, lambda args, ctx: ToolResult(content="done"))
        registry = ToolRegistry()
        registry.register_provider(p)
        result = await registry.execute(
            "echo", {}, caller_entity_id=1, discussion_id=1,
            progress_callback=lambda p, t, m: received.append(m),
        )
        assert result.content == "done"
```

**Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_tools.py::TestProgressCallback -v`
Expected: FAIL — `progress_callback` parameter not accepted.

**Step 3: Update signatures**

In `consensus/tools.py`:

1. Update `ToolProvider.execute()` signature (line ~101):
```python
@abstractmethod
async def execute(self, tool_name: str, arguments: dict, context: ToolContext,
                  progress_callback: Callable | None = None) -> ToolResult:
```

2. Update `PythonToolProvider.execute()` (line ~131):
```python
async def execute(self, tool_name: str, arguments: dict, context: ToolContext,
                  progress_callback: Callable | None = None) -> ToolResult:
```

3. Update `ToolRegistry.execute()` (line ~234) to accept and forward `progress_callback`:
```python
async def execute(self, tool_name: str, arguments: dict, caller_entity_id: int,
                  discussion_id: int, moderator_id: int | None = None,
                  progress_callback: Callable | None = None) -> ToolResult:
    # ... access control ...
    return await asyncio.wait_for(
        provider.execute(tool_name, arguments, ctx, progress_callback=progress_callback),
        timeout=TOOL_EXECUTION_TIMEOUT,
    )
```

**Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_tools.py -v`
Expected: ALL PASS (existing + new tests)

**Step 5: Commit**

```bash
git add consensus/tools.py tests/test_tools.py
git commit -m "feat: add progress_callback parameter to ToolProvider.execute interface"
```

---

### Task 5: `consult_expert` Meta-Tool

**Files:**
- Modify: `consensus/app.py`
- Test: `tests/test_app.py`

**Step 1: Write failing tests**

Add to `tests/test_app.py`:

```python
class TestConsultExpert:
    @pytest.mark.asyncio
    async def test_consult_expert_tool_registered(self, tmp_db):
        """The consult_expert tool should be in the tool registry."""
        app = ConsensusApp(db_path=str(tmp_db.db_path))
        tools = await app.tool_registry.list_all_tools()
        names = [t.name for t in tools]
        assert "consult_expert" in names

    @pytest.mark.asyncio
    async def test_consult_expert_unknown_expert(self, tmp_db):
        """Consulting a non-existent expert returns error."""
        app = ConsensusApp(db_path=str(tmp_db.db_path))
        ctx = ToolContext(caller_entity_id=1, discussion_id=1)
        result = await app._handle_consult_expert(
            {"expert_name": "nonexistent", "query": "test"}, ctx
        )
        assert result.is_error is True
        assert "not found" in result.content.lower()

    @pytest.mark.asyncio
    async def test_list_available_experts(self, tmp_db):
        """get_state should include available experts."""
        app = ConsensusApp(db_path=str(tmp_db.db_path))
        state = app.get_state()
        assert "experts" in state
```

**Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_app.py::TestConsultExpert -v`
Expected: FAIL

**Step 3: Implement consult_expert**

In `consensus/app.py`, add to `_init_builtin_tools()`:

```python
# Register consult_expert meta-tool
expert_tool_def = ToolDefinition(
    name="consult_expert",
    description="Consult a specialist expert for authoritative analysis. "
                "Available experts: use list_available_experts to see options.",
    parameters={
        "type": "object",
        "properties": {
            "expert_name": {
                "type": "string",
                "description": "Name of the expert to consult",
            },
            "query": {
                "type": "string",
                "description": "The question or claim to present to the expert",
            },
        },
        "required": ["expert_name", "query"],
    },
)
builtin_provider.register(expert_tool_def, self._handle_consult_expert)
```

Add the handler method:

```python
async def _handle_consult_expert(self, args: dict, context: ToolContext) -> ToolResult:
    """Handle consult_expert tool call — invoke an MCP expert."""
    expert_name = args.get("expert_name", "")
    query = args.get("query", "")

    # Find expert entity by name
    entities = self.db.get_entities()
    expert_entity = None
    for e in entities:
        if e["name"].lower() == expert_name.lower() and e.get("entity_type") == "expert":
            expert_entity = e
            break

    if not expert_entity:
        available = [e["name"] for e in entities if e.get("entity_type") == "expert"]
        return ToolResult(
            content=f"Expert '{expert_name}' not found. Available experts: {', '.join(available) or 'none'}",
            is_error=True,
        )

    # Get expert definition (MCP server + tool mapping)
    defn = self.db.get_expert_definition(expert_entity["id"])
    if not defn:
        return ToolResult(content=f"Expert '{expert_name}' has no MCP configuration", is_error=True)

    # Get or create MCP provider
    provider = self._get_mcp_provider(defn["mcp_server_id"])
    if not provider:
        return ToolResult(content=f"MCP server for '{expert_name}' is not available", is_error=True)

    # Build arguments (defaults + query)
    tool_args = dict(defn["default_arguments"])
    tool_args["claim"] = query  # TODO: make the query parameter name configurable

    # Progress callback emits events
    def on_progress(progress, total, message):
        self.emit("tool_progress", {
            "discussion_id": context.discussion_id,
            "entity_name": expert_entity["name"],
            "tool_name": defn["tool_name"],
            "progress": progress,
            "total": total,
            "message": message,
        })

    # Execute with expert-specific timeout
    timeout = defn.get("timeout_seconds", 300)
    try:
        result = await asyncio.wait_for(
            provider.execute(defn["tool_name"], tool_args, context, progress_callback=on_progress),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        return ToolResult(content=f"Expert '{expert_name}' timed out after {timeout}s", is_error=True)

    # Add expert message to discussion
    if self.discussion and self.discussion.id and not result.is_error:
        msg = Message(
            entity_id=expert_entity["id"],
            entity_name=expert_entity["name"],
            content=result.content,
            role=MessageRole.PARTICIPANT,
            timestamp=time.time(),
        )
        self.discussion.messages.append(msg)
        self.db.save_message(self.discussion.id, msg)
        self._notify()

    return result
```

Add MCP provider management:

```python
def _get_mcp_provider(self, server_id: int) -> Optional['MCPToolProvider']:
    """Get or create an MCPToolProvider for a server."""
    if not hasattr(self, '_mcp_providers'):
        self._mcp_providers: dict[int, Any] = {}
    return self._mcp_providers.get(server_id)
```

Update `get_state()` to include experts:

```python
# In get_state(), add:
experts = self.db.get_expert_definitions()
# ... include in returned dict:
"experts": experts,
```

**Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_app.py::TestConsultExpert -v`
Expected: PASS

**Step 5: Commit**

```bash
git add consensus/app.py tests/test_app.py
git commit -m "feat: add consult_expert meta-tool for invoking MCP experts"
```

---

### Task 6: MCP Server Lifecycle Management in ConsensusApp

**Files:**
- Modify: `consensus/app.py`
- Test: `tests/test_app.py`

**Step 1: Write failing tests**

Add to `tests/test_app.py`:

```python
class TestMCPServerManagement:
    def test_add_mcp_server(self, tmp_db):
        app = ConsensusApp(db_path=str(tmp_db.db_path))
        result = app.add_mcp_server("Test Server", "Desc", "echo", ["hello"])
        assert result is not None
        assert result["name"] == "Test Server"

    def test_get_mcp_servers(self, tmp_db):
        app = ConsensusApp(db_path=str(tmp_db.db_path))
        app.add_mcp_server("S1", "D1", "cmd1")
        app.add_mcp_server("S2", "D2", "cmd2")
        servers = app.get_mcp_servers()
        assert len(servers) == 2

    def test_update_mcp_server(self, tmp_db):
        app = ConsensusApp(db_path=str(tmp_db.db_path))
        result = app.add_mcp_server("Old", "Desc", "cmd")
        updated = app.update_mcp_server(result["id"], name="New")
        assert updated is True

    def test_delete_mcp_server(self, tmp_db):
        app = ConsensusApp(db_path=str(tmp_db.db_path))
        result = app.add_mcp_server("ToDelete", "Desc", "cmd")
        app.delete_mcp_server(result["id"])
        servers = app.get_mcp_servers()
        assert len(servers) == 0

    @pytest.mark.asyncio
    async def test_test_mcp_connection_bad_command(self, tmp_db):
        app = ConsensusApp(db_path=str(tmp_db.db_path))
        result = app.add_mcp_server("Bad", "Desc", "nonexistent_command_xyz")
        test_result = await app.test_mcp_connection(result["id"])
        assert test_result["success"] is False

    def test_save_expert_entity(self, tmp_db):
        app = ConsensusApp(db_path=str(tmp_db.db_path))
        server = app.add_mcp_server("Server", "Desc", "cmd")
        entity = app.save_entity("Fact Checker", "expert", "#00ff00")
        result = app.save_expert_definition(
            entity_id=entity["id"],
            mcp_server_id=server["id"],
            tool_name="fact_check_claim",
            description="Medical fact-checker",
        )
        assert result is not None
```

**Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_app.py::TestMCPServerManagement -v`
Expected: FAIL

**Step 3: Implement MCP server management methods**

Add to `ConsensusApp` in `consensus/app.py`:

```python
# --- MCP Server Management ---

def add_mcp_server(self, name: str, description: str, command: str,
                   args: list | None = None, env: dict | None = None) -> dict | None:
    server_id = self.db.add_mcp_server(name, description, command, args, env)
    self._notify()
    return self.db.get_mcp_server(server_id)

def get_mcp_servers(self) -> list[dict]:
    return self.db.get_mcp_servers()

def update_mcp_server(self, server_id: int, **kwargs) -> bool:
    result = self.db.update_mcp_server(server_id, **kwargs)
    self._notify()
    return result

def delete_mcp_server(self, server_id: int) -> bool:
    # Disconnect if running
    if hasattr(self, '_mcp_providers') and server_id in self._mcp_providers:
        asyncio.create_task(self._mcp_providers[server_id].close())
        del self._mcp_providers[server_id]
    result = self.db.delete_mcp_server(server_id)
    self._notify()
    return result

async def test_mcp_connection(self, server_id: int) -> dict:
    """Test connection to an MCP server — launch, initialize, list tools, shut down."""
    from .mcp_client import MCPToolProvider
    server = self.db.get_mcp_server(server_id)
    if not server:
        return {"success": False, "error": "Server not found"}
    provider = MCPToolProvider(
        name=server["name"], command=server["command"],
        args=server["args"], env=server["env"],
    )
    try:
        connected = await provider.connect()
        if not connected:
            return {"success": False, "error": "Failed to connect"}
        tools = await provider.list_tools()
        tool_info = [{"name": t.name, "description": t.description} for t in tools]
        return {"success": True, "tools": tool_info}
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        await provider.close()

# --- Expert Definition Management ---

def save_expert_definition(self, entity_id: int, mcp_server_id: int, tool_name: str,
                           description: str = "", default_arguments: dict | None = None,
                           timeout_seconds: int = 300) -> dict | None:
    eid = self.db.add_expert_definition(entity_id, mcp_server_id, tool_name,
                                         description, default_arguments, timeout_seconds)
    self._notify()
    return self.db.get_expert_definition(entity_id)

def get_expert_definitions(self) -> list[dict]:
    return self.db.get_expert_definitions()

async def connect_mcp_server(self, server_id: int) -> bool:
    """Connect to an MCP server and register its provider."""
    from .mcp_client import MCPToolProvider
    server = self.db.get_mcp_server(server_id)
    if not server or not server["enabled"]:
        return False
    provider = MCPToolProvider(
        name=server["name"], command=server["command"],
        args=server["args"], env=server["env"],
    )
    connected = await provider.connect()
    if connected:
        if not hasattr(self, '_mcp_providers'):
            self._mcp_providers = {}
        self._mcp_providers[server_id] = provider
        self.tool_registry.register_provider(provider)
    return connected
```

**Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_app.py::TestMCPServerManagement -v`
Expected: PASS

**Step 5: Commit**

```bash
git add consensus/app.py tests/test_app.py
git commit -m "feat: add MCP server lifecycle management to ConsensusApp"
```

---

### Task 7: Desktop Bridge — Progress Event Forwarding

**Files:**
- Modify: `consensus/desktop.py`
- Test: Manual testing (pywebview JS evaluation is hard to unit test)

**Step 1: Add progress event subscription**

In `consensus/desktop.py`, in `DesktopBridge.__init__()`, after `app.set_update_callback(self._push_state)`:

```python
app.on("tool_progress", self._push_progress)
```

Add the handler:

```python
def _push_progress(self, data: dict) -> None:
    """Forward tool progress events to the JS frontend."""
    if self._window is None:
        return
    try:
        payload = json.dumps(data)
        self._window.evaluate_js(f"if(window.onToolProgress) onToolProgress({payload})")
    except Exception:
        logger.debug("Failed to push progress event")
```

**Step 2: Expose MCP management methods to JS**

Add bridge methods matching Task 6's ConsensusApp methods:

```python
def get_mcp_servers(self) -> list:
    return self.app.get_mcp_servers()

def add_mcp_server(self, name, description, command, args=None, env=None) -> dict:
    return self.app.add_mcp_server(name, description, command, args, env)

def update_mcp_server(self, server_id, **kwargs) -> bool:
    return self.app.update_mcp_server(server_id, **kwargs)

def delete_mcp_server(self, server_id) -> bool:
    return self.app.delete_mcp_server(server_id)

def test_mcp_connection(self, server_id) -> dict:
    return self._run_async(self.app.test_mcp_connection(server_id))

def save_expert_definition(self, entity_id, mcp_server_id, tool_name,
                           description="", default_arguments=None, timeout_seconds=300) -> dict:
    return self.app.save_expert_definition(entity_id, mcp_server_id, tool_name,
                                            description, default_arguments, timeout_seconds)

def consult_expert(self, expert_name, query) -> dict:
    return self._run_async(self.app.consult_expert(expert_name, query))
```

**Step 3: Commit**

```bash
git add consensus/desktop.py
git commit -m "feat: add MCP progress forwarding and management to desktop bridge"
```

---

### Task 8: Web Server — SSE Endpoint & MCP Routes

**Files:**
- Modify: `consensus/server.py`
- Test: `tests/test_server.py` (if exists, otherwise manual)

**Step 1: Add SSE endpoint**

In `consensus/server.py`, add a new handler:

```python
async def handle_events(request: web.Request) -> web.StreamResponse:
    """SSE endpoint for real-time events (tool progress, etc.)."""
    app = get_app(request)

    resp = web.StreamResponse()
    resp.headers["Content-Type"] = "text/event-stream"
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["Connection"] = "keep-alive"
    await resp.prepare(request)

    queue: asyncio.Queue = asyncio.Queue()

    def on_event(data: dict) -> None:
        queue.put_nowait(data)

    app.on("tool_progress", on_event)
    try:
        while True:
            try:
                data = await asyncio.wait_for(queue.get(), timeout=30)
                event_str = f"event: tool_progress\ndata: {json.dumps(data)}\n\n"
                await resp.write(event_str.encode())
            except asyncio.TimeoutError:
                # Send keepalive comment
                await resp.write(b": keepalive\n\n")
    except (asyncio.CancelledError, ConnectionResetError):
        pass
    finally:
        app.off("tool_progress", on_event)

    return resp
```

**Step 2: Add MCP management routes**

The existing `handle_api` dispatcher already maps method names to `ConsensusApp` methods. The new methods (`add_mcp_server`, `get_mcp_servers`, `update_mcp_server`, `delete_mcp_server`, `test_mcp_connection`, `save_expert_definition`, `consult_expert`) will be automatically dispatched. No new route code needed — just verify the method names match.

**Step 3: Register the SSE route**

In the route setup section of `launch_web()`:

```python
webapp.router.add_get("/api/events", handle_events)
```

Add this BEFORE the catch-all static file route.

**Step 4: Commit**

```bash
git add consensus/server.py
git commit -m "feat: add SSE endpoint for real-time progress events in web mode"
```

---

### Task 9: Frontend — Progress Indicator

**Files:**
- Modify: `consensus/static/app.js`

**Step 1: Add `onToolProgress` handler**

Add near the existing `onStateUpdate` function:

```javascript
function onToolProgress(data) {
    const indicator = document.getElementById('typing-indicator');
    if (!indicator) return;

    const { entity_name, message, progress, total } = data;

    let progressText = message || 'Working...';
    let barHtml = '';

    if (total && total > 0) {
        const pct = Math.round((progress / total) * 100);
        progressText += ` (${progress}/${total})`;
        barHtml = `<div class="progress-bar-container">
            <div class="progress-bar-fill" style="width: ${pct}%"></div>
        </div>`;
    }

    indicator.innerHTML = `
        <div class="expert-progress">
            <span class="typing-name">${escHtml(entity_name)}</span>:
            <span class="typing-status">${escHtml(progressText)}</span>
            ${barHtml}
        </div>`;
    show(indicator);
}
```

**Step 2: Add SSE connection for web mode**

In the `WebAPI` class or initialization section:

```javascript
// Connect to SSE for real-time events (web mode only)
if (typeof window.pywebview === 'undefined') {
    const evtSource = new EventSource('/api/events');
    evtSource.addEventListener('tool_progress', (e) => {
        const data = JSON.parse(e.data);
        onToolProgress(data);
    });
    evtSource.onerror = () => {
        // EventSource auto-reconnects; just log
        console.debug('SSE connection lost, reconnecting...');
    };
}
```

**Step 3: Add CSS for the progress bar**

Add to `consensus/static/style.css` (or inline in app.js if styles are managed there):

```css
.expert-progress {
    display: flex;
    flex-direction: column;
    gap: 0.25rem;
    padding: 0.5rem;
}

.progress-bar-container {
    width: 100%;
    height: 0.4rem;
    background: var(--bg-tertiary, #e0e0e0);
    border-radius: 0.2rem;
    overflow: hidden;
}

.progress-bar-fill {
    height: 100%;
    background: var(--accent-color, #4a9eff);
    border-radius: 0.2rem;
    transition: width 0.3s ease;
}
```

**Step 4: Add "Consult Expert" button to discussion UI**

Add a button near the message input area. When clicked, show a dropdown of available experts with descriptions, then a text field for the query:

```javascript
function renderConsultExpertButton(experts) {
    if (!experts || experts.length === 0) return '';
    return `<button class="btn btn-secondary" onclick="showConsultExpertDialog()"
            title="Consult a specialist expert">Consult Expert</button>`;
}

async function showConsultExpertDialog() {
    const experts = state.experts || [];
    if (experts.length === 0) {
        showToast('No experts configured');
        return;
    }
    // Build a simple dialog with expert selection and query input
    // ... (dialog HTML with select dropdown + textarea + submit button)
}

async function submitExpertConsultation(expertName, query) {
    showTypingIndicator(expertName, 'consulting...');
    try {
        await api.consultExpert(expertName, query);
    } catch (e) {
        showToast('Expert consultation failed: ' + e.message);
    }
}
```

Add the API method to both `DesktopAPI` and `WebAPI`:

```javascript
// DesktopAPI
async consultExpert(expertName, query) {
    return await window.pywebview.api.consult_expert(expertName, query);
}

// WebAPI
async consultExpert(expertName, query) {
    return await this._post('consult_expert', { expert_name: expertName, query: query });
}
```

**Step 5: Commit**

```bash
git add consensus/static/app.js consensus/static/style.css
git commit -m "feat: add expert progress indicator and consult expert UI"
```

---

### Task 10: Frontend — MCP Server Management UI

**Files:**
- Modify: `consensus/static/app.js`

**Step 1: Add "MCP Servers" section to Providers tab**

Add a section that renders the list of MCP servers from `state.mcp_servers` with add/edit/delete/toggle controls. Include a "Test Connection" button per server.

```javascript
function renderMCPServers(servers) {
    // ... render table/list of servers
    // Each row: name, description, command, enabled toggle, test/edit/delete buttons
}

async function addMCPServer() {
    // Dialog with fields: name, description, command, args (JSON), env (JSON)
    // Calls api.addMcpServer(...)
}

async function testMCPConnection(serverId) {
    showToast('Testing connection...');
    const result = await api.testMcpConnection(serverId);
    if (result.success) {
        showToast(`Connected! Found ${result.tools.length} tools: ${result.tools.map(t => t.name).join(', ')}`);
    } else {
        showToast(`Connection failed: ${result.error}`);
    }
}
```

Add API methods:

```javascript
// DesktopAPI
async getMcpServers() { return await window.pywebview.api.get_mcp_servers(); }
async addMcpServer(name, description, command, args, env) {
    return await window.pywebview.api.add_mcp_server(name, description, command, args, env);
}
async updateMcpServer(serverId, updates) {
    return await window.pywebview.api.update_mcp_server(serverId, updates);
}
async deleteMcpServer(serverId) {
    return await window.pywebview.api.delete_mcp_server(serverId);
}
async testMcpConnection(serverId) {
    return await window.pywebview.api.test_mcp_connection(serverId);
}

// WebAPI — same pattern via this._post()
```

**Step 2: Add expert entity type to Profiles tab**

When creating/editing an entity, add a dropdown for `entity_type` (ai/human/expert). When "expert" is selected, show additional fields: MCP server selection, tool name, description, default arguments, timeout.

**Step 3: Wire state updates**

Ensure `get_state()` includes `mcp_servers` and `experts`. The `renderProviders()` function should call `renderMCPServers()`.

**Step 4: Commit**

```bash
git add consensus/static/app.js
git commit -m "feat: add MCP server management and expert configuration UI"
```

---

### Task 11: Integration Test — End-to-End Expert Consultation

**Files:**
- Create: `tests/test_mcp_integration.py`

**Step 1: Write an integration test using a simple mock MCP server**

Create a minimal MCP server script that can be used in tests:

```python
"""Integration test for MCP expert consultation flow."""

import asyncio
import json
import os
import sys
import tempfile
import pytest
from pathlib import Path

from consensus.app import ConsensusApp
from consensus.mcp_client import MCPToolProvider

# Path to our test MCP server script
TEST_SERVER_SCRIPT = Path(__file__).parent / "fixtures" / "mock_mcp_server.py"


@pytest.fixture
def mock_server_script(tmp_path):
    """Create a minimal MCP server script for testing."""
    script = tmp_path / "mock_mcp_server.py"
    script.write_text('''
import json
import sys

def send(msg):
    sys.stdout.write(json.dumps(msg) + "\\n")
    sys.stdout.flush()

def main():
    while True:
        line = sys.stdin.readline()
        if not line:
            break
        msg = json.loads(line.strip())

        if msg.get("method") == "initialize":
            send({"jsonrpc": "2.0", "id": msg["id"], "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "test-server", "version": "1.0"},
            }})
        elif msg.get("method") == "tools/list":
            send({"jsonrpc": "2.0", "id": msg["id"], "result": {"tools": [
                {"name": "test_tool", "description": "A test tool",
                 "inputSchema": {"type": "object", "properties": {
                     "query": {"type": "string"}
                 }, "required": ["query"]}},
            ]}})
        elif msg.get("method") == "tools/call":
            # Send progress if token provided
            token = msg.get("params", {}).get("_meta", {}).get("progressToken")
            if token:
                send({"jsonrpc": "2.0", "method": "notifications/progress",
                      "params": {"progressToken": token, "progress": 1, "total": 2,
                                 "message": "Step 1..."}})
                send({"jsonrpc": "2.0", "method": "notifications/progress",
                      "params": {"progressToken": token, "progress": 2, "total": 2,
                                 "message": "Step 2..."}})
            send({"jsonrpc": "2.0", "id": msg["id"], "result": {
                "content": [{"type": "text", "text": "Test result for: " + msg["params"]["arguments"].get("query", "")}],
            }})

if __name__ == "__main__":
    main()
''')
    return str(script)


class TestMCPIntegration:
    @pytest.mark.asyncio
    async def test_connect_and_list_tools(self, mock_server_script):
        provider = MCPToolProvider(
            name="test", command=sys.executable, args=[mock_server_script],
        )
        connected = await provider.connect()
        assert connected is True

        tools = await provider.list_tools()
        assert len(tools) == 1
        assert tools[0].name == "test_tool"
        await provider.close()

    @pytest.mark.asyncio
    async def test_execute_with_progress(self, mock_server_script):
        provider = MCPToolProvider(
            name="test", command=sys.executable, args=[mock_server_script],
        )
        await provider.connect()

        progress_events = []
        from consensus.tools import ToolContext
        ctx = ToolContext(caller_entity_id=1, discussion_id=1)
        result = await provider.execute(
            "test_tool", {"query": "hello"},
            ctx, progress_callback=lambda p, t, m: progress_events.append((p, t, m)),
        )
        assert "hello" in result.content
        assert len(progress_events) == 2
        assert progress_events[0] == (1, 2, "Step 1...")
        await provider.close()
```

**Step 2: Run integration test**

Run: `python -m pytest tests/test_mcp_integration.py -v`
Expected: PASS (this validates the full stdio JSON-RPC flow)

**Step 3: Commit**

```bash
git add tests/test_mcp_integration.py
git commit -m "test: add MCP integration test with mock stdio server"
```

---

### Task 12: Update `get_state()` & Wire Everything Together

**Files:**
- Modify: `consensus/app.py`
- Modify: `consensus/server.py` (if needed)

**Step 1: Update `get_state()` to include MCP servers and experts**

In `consensus/app.py`, update `get_state()`:

```python
def get_state(self) -> dict:
    # ... existing state ...
    state["mcp_servers"] = self.db.get_mcp_servers()
    state["experts"] = self.db.get_expert_definitions()
    return state
```

**Step 2: Add a `consult_expert` method on ConsensusApp for human invocation**

This is the endpoint called from the UI button (not via the tool loop):

```python
async def consult_expert(self, expert_name: str, query: str) -> dict:
    """Human-initiated expert consultation."""
    from .tools import ToolContext
    ctx = ToolContext(
        caller_entity_id=0,  # human caller
        discussion_id=self.discussion.id if self.discussion else 0,
    )
    result = await self._handle_consult_expert(
        {"expert_name": expert_name, "query": query}, ctx,
    )
    return {"content": result.content, "is_error": result.is_error}
```

**Step 3: Auto-connect MCP servers on startup**

In `ConsensusApp.__init__()` or a new `async init()` method, connect to enabled MCP servers:

```python
async def connect_mcp_servers(self) -> None:
    """Connect to all enabled MCP servers."""
    for server in self.db.get_mcp_servers(enabled_only=True):
        try:
            await self.connect_mcp_server(server["id"])
        except Exception:
            logger.warning("Failed to connect MCP server: %s", server["name"])
```

Note: Since `__init__` is sync, this must be called separately after the event loop is available. In desktop mode, call from `DesktopBridge` after window creation. In web mode, call during `launch_web()` startup.

**Step 4: Run full test suite**

Run: `python -m pytest tests/ -v`
Expected: ALL PASS

**Step 5: Commit**

```bash
git add consensus/app.py consensus/desktop.py consensus/server.py
git commit -m "feat: wire MCP expert system into state, startup, and human invocation"
```

---

### Task 13: Expert Message Rendering

**Files:**
- Modify: `consensus/static/app.js`
- Modify: `consensus/static/style.css`

**Step 1: Add distinct rendering for expert messages**

In the `renderMessage()` function, detect expert entity type and add a badge:

```javascript
// In renderMessage(), after building the message header:
const entity = getEntity(msg.entity_id);
const isExpert = entity && entity.entity_type === 'expert';
const expertBadge = isExpert ? '<span class="expert-badge">Expert</span>' : '';
// Include expertBadge in the header HTML
```

**Step 2: Style the expert badge**

```css
.expert-badge {
    display: inline-block;
    font-size: 0.7rem;
    padding: 0.1rem 0.4rem;
    border-radius: 0.2rem;
    background: var(--accent-color, #4a9eff);
    color: white;
    margin-left: 0.3rem;
    vertical-align: middle;
}
```

**Step 3: Commit**

```bash
git add consensus/static/app.js consensus/static/style.css
git commit -m "feat: add expert badge and distinct rendering for expert messages"
```

---

### Task 14: Progress Stall Detection

**Files:**
- Modify: `consensus/static/app.js`

**Step 1: Add stall timer**

When a progress event arrives, reset a 60-second timer. If no new event arrives within 60s, update the indicator to show "Still working...":

```javascript
let progressStallTimer = null;

function onToolProgress(data) {
    // ... existing progress rendering ...

    // Reset stall timer
    if (progressStallTimer) clearTimeout(progressStallTimer);
    progressStallTimer = setTimeout(() => {
        const indicator = document.getElementById('typing-indicator');
        if (indicator && indicator.querySelector('.expert-progress')) {
            indicator.querySelector('.typing-status').textContent = 'Still working...';
        }
    }, 60000);
}
```

**Step 2: Clear timer on state update**

In `onStateUpdate()`, clear the stall timer:

```javascript
if (progressStallTimer) {
    clearTimeout(progressStallTimer);
    progressStallTimer = null;
}
```

**Step 3: Commit**

```bash
git add consensus/static/app.js
git commit -m "feat: add 60s stall detection for expert progress indicator"
```

---

### Task 15: Final Integration & Manual Testing

**Files:** No new files — verification only.

**Step 1: Run full test suite**

Run: `python -m pytest tests/ -v`
Expected: ALL PASS

**Step 2: Manual smoke test (desktop mode)**

1. `python -m consensus`
2. Go to Providers tab → MCP Servers section
3. Add a server (e.g., `uvx bmlibrarian-mcp` if available, or use the mock script)
4. Click "Test Connection" — verify tools are discovered
5. Go to Profiles tab → Create expert entity → link to MCP server + tool
6. Start a discussion → click "Consult Expert" → submit a query
7. Verify progress indicator shows stages and progress bar
8. Verify expert message appears in discussion with badge

**Step 3: Manual smoke test (web mode)**

1. `python -m consensus --web`
2. Repeat the same steps in browser
3. Verify SSE connection works (check Network tab for `/api/events`)
4. Verify progress updates arrive in real-time

**Step 4: Final commit if any fixes needed**

```bash
git add -A
git commit -m "fix: address issues found during integration testing"
```
