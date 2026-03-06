# 3. Backend Modules

[Back to index](programmer-manual.md) | [Previous: Architecture](02-architecture.md) | [Next: Frontend](04-frontend.md)

---

## `app.py` -- The Orchestrator

`ConsensusApp` is the **central controller**. Both the web server and the
desktop bridge delegate to this class. It owns the `Database`, the current
`Discussion`, the `Moderator`, and the `ToolRegistry`.

**Initialisation:**
```python
class ConsensusApp:
    def __init__(self, db_path=""):
        self.db = Database(db_path or get_db_path())
        self.discussion = Discussion()
        self.moderator = Moderator(self.discussion, self.db)
        self.tool_registry = ToolRegistry(self.db)
        self._on_update = None  # callback for push-based state updates
        self._init_builtin_tools()  # registers web search, etc.
```

**Update callback:** `set_update_callback(callback)` registers a function
that receives the full state dict whenever something changes. The desktop
bridge uses this to push state to the webview via `evaluate_js`. The web
server doesn't use it -- it returns state in each HTTP response instead.

**`get_state()`** assembles the complete frontend state:
```python
def get_state(self):
    state = self.discussion.to_dict()
    state["providers"] = self.get_providers()
    state["saved_entities"] = self.db.get_entities()
    state["prompts"] = self.db.get_prompts()
    state["discussions_history"] = self.db.get_discussions()
    return state
```

### Method groups

| Group | Methods |
|-------|---------|
| **Provider management** | `add_provider`, `update_provider`, `delete_provider`, `get_providers`, `fetch_models` |
| **Entity management** | `save_entity`, `delete_entity`, `reactivate_entity`, `get_entities`, `get_inactive_entities` |
| **Prompt management** | `save_prompt`, `delete_prompt`, `get_prompts` |
| **Discussion setup** | `add_to_discussion`, `remove_from_discussion`, `set_moderator`, `set_topic` |
| **Discussion lifecycle** | `start_discussion`, `submit_human_message`, `submit_moderator_message`, `generate_ai_turn`, `complete_turn`, `reassign_turn`, `mediate`, `conclude_discussion`, `pause_discussion`, `resume_discussion`, `reopen_discussion` |
| **Dynamic participation** | `add_participant`, `remove_participant` |
| **Tool management** | `list_available_tools`, `get_entity_tools`, `assign_tool_to_entity`, `remove_entity_tool`, `set_discussion_tool_override` |
| **BYOK** | `set_request_api_keys`, `clear_request_api_keys`, `resolve_provider_api_key` |
| **History / export** | `load_discussion`, `get_export_data`, `reset` |

### Entity soft-delete

`delete_entity()` implements smart deletion:
- If the entity is **not** referenced in any past discussion: hard-delete from DB
- If the entity **is** referenced: soft-delete (set `active=0`) to preserve
  foreign key integrity
- Returns `{"deleted": true}` or `{"deactivated": true}` to inform the UI

`reactivate_entity()` reverses a soft-delete. `get_inactive_entities()` returns
all deactivated profiles for the reactivation UI.

### Pause and resume

- `pause_discussion()` -- sets status to `paused`, logs a system message,
  persists turn state. Participants can be added/removed while paused.
- `resume_discussion()` -- restores status to `active`, logs a system message,
  re-establishes turn continuity.
- `reopen_discussion()` -- transitions a `concluded` discussion back to
  `active` for further turns.

### BYOK (Bring Your Own Key)

In multi-user mode, API keys are provided per-request:
- `set_request_api_keys(keys_dict)` -- stores keys in a `contextvars.ContextVar`
  (request-scoped, no cross-request leakage)
- `clear_request_api_keys()` -- clears the context var after the request
- `resolve_provider_api_key(provider_id)` -- checks BYOK keys first, falls
  back to environment variables

**Security:** `_provider_for_frontend()` strips the `api_key_env` field from
provider data before sending it to the frontend, replacing it with a boolean
`has_key` flag.

---

## `server.py` -- Web Mode (aiohttp)

The web server is a single async function `launch_web(host, port, multi_user)`
that:

1. Creates either a shared `ConsensusApp` (single-user) or a `SessionManager`
   (multi-user)
2. Sets up middleware: security headers, CORS, rate limiting
3. Defines a dispatch-style `handle_api()` handler
4. Serves static files with path traversal protection
5. Provides a `GET /health` endpoint for load balancer checks
6. Blocks until interrupted

### Single-user vs multi-user

When `multi_user=False` (default), a single `ConsensusApp` is shared by all
clients (suitable for local/desktop use). When `multi_user=True`, each browser
session gets its own `ConsensusApp` via `SessionManager`, identified by a
`consensus_sid` cookie.

### Middleware stack (applied in order)

| Middleware | Purpose |
|------------|---------|
| `security_headers_middleware` | Adds `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy` |
| `cors_middleware` | Rejects API requests from unknown origins. Allows local origin in single-user mode; checks `CONSENSUS_ALLOWED_ORIGINS` in multi-user mode |
| `rate_limit_middleware` | Per-session/IP rate limiting (120 requests per 60s window) on `/api/` routes |

### BYOK request flow

In multi-user mode, the frontend sends API keys via an `X-API-Keys` header
(JSON-encoded map of `provider_id -> key`). The server:
1. Extracts keys via `_extract_api_keys(request)`
2. Sets keys via `app.set_request_api_keys()` before the handler
3. Clears keys via `app.clear_request_api_keys()` in a `finally` block

Keys are never logged or persisted.

### API routing

All API calls are `POST /api/{method}` where `{method}` maps to a key in a
`handlers` dict. The handler dict maps method names to lambdas that call
`ConsensusApp` methods with the appropriate arguments from the JSON request
body.

```python
handlers = {
    "get_state": lambda: app.get_state(),
    "add_provider": lambda: app.add_provider(data["name"], data["base_url"], ...),
    ...
}
```

If a handler returns a coroutine (e.g., `generate_ai_turn`), it is awaited.

**Response format:** Every successful API response wraps the result:
```json
{"result": <return value>, "state": <full app state>}
```

This means the frontend always receives the latest state after any mutation.

### Session cookies

In multi-user mode, responses include a `consensus_sid` cookie (httponly,
SameSite=Lax). The cookie is validated against a regex pattern and refreshed
on each request.

### Non-API endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/health` | GET | Health check (returns `{"status": "ok"}` or `{"status": "ok", "active_sessions": N}` in multi-user mode) |

---

## `session.py` -- Multi-User Session Management

`SessionManager` manages per-user `ConsensusApp` instances for multi-user
deployments.

**Key design:**
- Each session gets its own `ConsensusApp` and SQLite database file stored in a
  configurable directory (`CONSENSUS_SESSION_DIR` env var or
  `<data_dir>/sessions/`)
- Sessions are identified by cryptographically random URL-safe tokens
  (validated by regex: `^[A-Za-z0-9_-]{20,64}$`)
- Sessions expire after `DEFAULT_SESSION_TTL` (24 hours) of inactivity
- Maximum concurrent sessions capped at `DEFAULT_MAX_SESSIONS` (100)
- A background asyncio task runs cleanup every 5 minutes, removing expired
  sessions and their SQLite files

**Key methods:**

| Method | Purpose |
|--------|---------|
| `get_app(session_id)` | Returns the `ConsensusApp` for a session (creates if new), or `None` if at capacity |
| `is_valid_session_id(sid)` | Validates session ID format |
| `start_cleanup_loop()` | Starts the periodic background cleanup task |
| `close_all()` | Closes all sessions and stops the cleanup loop |

---

## `desktop.py` -- Desktop Mode (pywebview)

The desktop mode uses pywebview to render the same HTML/CSS/JS in a native
window. The key challenge is bridging pywebview's synchronous JS-to-Python
calls with the async application methods.

**`DesktopBridge`** is the JS API object passed to pywebview. Every public
method (no leading underscore) becomes callable from JavaScript as
`window.pywebview.api.<method_name>(...)`.

**Async bridging:** A background `asyncio` event loop runs in a daemon thread.
Async methods are submitted with `asyncio.run_coroutine_threadsafe()` and
blocked on with `future.result(timeout=180)`.

```python
def _run_async(self, coro):
    future = asyncio.run_coroutine_threadsafe(coro, self._loop)
    return future.result(timeout=ASYNC_BRIDGE_TIMEOUT)
```

**Push-based updates:** `_push_state()` calls `window.evaluate_js()` to
invoke `onStateUpdate(state)` in the frontend whenever state changes.

**Tool bridge methods:** `DesktopBridge` includes methods for tool management:
`list_available_tools()`, `get_entity_tools()`, `assign_tool()`,
`remove_entity_tool()`, `set_discussion_tool_override()`. These mirror the
corresponding `ConsensusApp` methods.

**`launch_desktop()`** creates the pywebview window (1280x800, min 900x600)
pointing at `static/index.html`, wires up the bridge, and starts the webview
event loop.

---

## `__main__.py` -- Entry Point

Parses command-line arguments (`--web`, `--host`, `--port`, `--multi-user`,
`--debug`), calls `load_env()` to load `~/.consensus/.env`, and dispatches to
either `launch_web(host, port, multi_user)` or `launch_desktop()`. Prints a
helpful error if the required optional dependency is not installed.

---

[Next: Frontend](04-frontend.md)
