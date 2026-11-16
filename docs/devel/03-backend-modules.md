# 3. Backend Modules

[Back to index](programmer-manual.md) | [Previous: Architecture](02-architecture.md) | [Next: Frontend](04-frontend.md)

---

## `app.py` -- The Orchestrator

`ConsensusApp` is the **central controller**. Both the web server and the
desktop bridge delegate to this class. It owns the `Database`, the current
`Discussion`, the `Moderator`, and the `ToolRegistry`.

**Module decomposition:** `ConsensusApp` methods are split across domain modules
to keep file sizes manageable:
- `app_providers.py` — provider management
- `app_entities.py` — entity CRUD
- `app_discussion_setup.py` — discussion creation & configuration
- `app_discussion_roles.py` — participant roles: single devil's advocate,
  its auto-assigned tools and last place in the turn order
- `app_discussion_rotation.py` — `update_rotation()`: every mid-discussion
  membership or role change goes through it, so `turn_order`,
  `base_turn_order`, the `_turn_order`/`_turn_index` bookkeeping and
  `discussion_members.turn_position` stay in step (issue #104)
- `app_discussion_flow/` — turn flow package: `submissions.py` (human and
  moderator message submission), `turns.py` (AI generation, turn
  completion, reassignment), `method_switch.py` (the Triage handoff and
  its recovery path), `conclusion.py` (mediation, final synthesis) and
  `helpers.py` (pass detection, error description, cost, turn order).
  The package re-exports the public API, so
  `from consensus.app_discussion_flow import complete_turn` works unchanged
- `app_discussion_state.py` — discussion state management

These modules contain standalone functions that `ConsensusApp` delegates to.

**Mixins (`app_api/`, issue #61):** `ConsensusApp`'s public methods are
grouped into mixin classes it inherits from, the same way `Database`
composes the `db/` mixins: `mcp_experts.py` (MCP servers, expert
definitions, `consult_expert`), `catalog.py` (providers, entities,
prompts), `discussion.py` (roster/topic/method/context-strategy setup and
the turn lifecycle), `history.py` (load, pause/resume/reopen, ask_user
input, reset), `tool_assignments.py`, `documents.py` and `images.py`.
`app.py` itself keeps construction, tool registration, BYOK key
resolution, the event emitter and `get_state()`. The mixins own no
state; each docstring lists the attributes it expects from the host.

**Initialisation** (abridged from `app.py`):
```python
class ConsensusApp(MCPExpertsMixin, CatalogMixin, DiscussionMixin, HistoryMixin,
                   ToolAssignmentsMixin, DocumentsMixin, ImagesMixin):
    def __init__(self, db_path=""):
        self.db = Database(db_path or get_db_path())  # db.pricing is the PricingCache
        self._refresh_pricing_if_needed()
        self.db.purge_deleted_discussions()
        self.discussion = Discussion()
        self.tool_registry = ToolRegistry(db=self.db)
        self.moderator = Moderator(self.discussion, self.db, key_resolver=...,
                                   tool_registry=self.tool_registry, progress_callback=...)
        self._on_update = None          # callback for push-based state updates
        self._event_listeners = {}      # lightweight event emitter
        self._mcp_providers = {}        # active MCP server connections
        self._pending_user_inputs = {}  # ask_user futures awaiting the human
        self._init_builtin_tools()      # web search; consult_expert / list_available_experts
        self._init_interactive_tools()  # ask_user
        self._init_memory_tools()       # memory tools (if sqlite-vec installed)
        self._init_document_tools()     # document RAG
        self._init_image_tools()        # image tools
        self._init_python_tools()       # execute_python, install_python_package
        self._load_mcp_config()         # load MCP servers from config files
```

**Update callback:** `set_update_callback(callback)` registers a function
that receives the full state dict whenever something changes. The desktop
bridge uses this to push state to the webview via `evaluate_js`. The web
server doesn't use it -- it returns state in each HTTP response instead.

**Event emitter:** `on(event, handler)` and `emit(event, data)` provide a
lightweight pub/sub system for real-time notifications. Used by the SSE
endpoint to forward `tool_progress` events to connected browsers.

**`get_state()`** assembles the complete frontend state:
```python
def get_state(self):
    state = self.discussion.to_dict()
    state["providers"] = self.get_providers()
    state["saved_entities"] = self.db.get_entities()
    state["prompts"] = self.db.get_prompts()
    state["discussions_history"] = self.db.get_discussions()
    state["mcp_servers"] = self.get_mcp_servers()
    state["experts"] = self.get_expert_definitions()
    return state
```

### Method groups

| Group | Methods |
|-------|---------|
| **Provider management** | `add_provider`, `update_provider`, `delete_provider`, `get_providers`, `fetch_models` |
| **Entity management** | `save_entity`, `delete_entity`, `reactivate_entity`, `get_entities`, `get_inactive_entities` |
| **Prompt management** | `save_prompt`, `delete_prompt`, `get_prompts` |
| **Discussion setup** | `add_to_discussion`, `remove_from_discussion`, `set_moderator`, `set_participant_role`, `set_topic`, `set_cost_limit`, `list_discussion_methods`, `set_discussion_method`, `recommend_method`, `set_default_context_strategy`, `set_member_context_strategy` |
| **Discussion lifecycle** | `start_discussion`, `submit_human_message`, `submit_human_structured_message`, `submit_moderator_message`, `generate_ai_turn`, `complete_turn`, `reassign_turn`, `retry_method_switch`, `continue_discussion`, `mediate`, `conclude_discussion`, `pause_discussion`, `resume_discussion`, `reopen_discussion` |
| **Interactive input** | `submit_user_input`, `cancel_user_input` (answers to an AI's `ask_user` call) |
| **Tool management** | `list_available_tools`, `get_entity_tools`, `assign_tool_to_entity`, `remove_entity_tool`, `set_discussion_tool_override` |
| **MCP server management** | `add_mcp_server`, `get_mcp_servers`, `update_mcp_server`, `delete_mcp_server`, `test_mcp_connection`, `connect_mcp_server` |
| **Expert definitions** | `save_expert_definition`, `get_expert_definitions`, `consult_expert` |
| **Document management** | `add_document`, `add_document_from_url`, `get_discussion_documents`, `remove_document`, `delete_document` |
| **Image management** | `add_image`, `add_image_from_url`, `get_discussion_images`, `remove_discussion_image`, `get_image_data`, `delete_image` |
| **Cost tracking** | Automatic via `PricingCache` during message generation |
| **Events** | `on`, `off`, `emit` — lightweight pub/sub for real-time updates |
| **BYOK** | `set_request_api_keys`, `clear_request_api_keys`, `resolve_provider_api_key` |
| **History / export** | `load_discussion`, `get_export_data`, `delete_discussions`, `restore_discussion`, `reset` |

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

## `server/` -- Web Mode (aiohttp)

`launch_web(host, port, multi_user)` (`app_factory.py`) loads `.env`, builds
the app with `create_web_app()` and serves it until interrupted.
`create_web_app()` is also what the route tests drive (`tests/test_server_*`
through aiohttp's `TestClient`).

| Module | Contents |
|--------|----------|
| `app_factory.py` | `create_web_app()` (route table, `/eval/` mount, `on_startup`/`on_cleanup`), `launch_web()` |
| `context.py` | `ServerContext` (all per-server state), `CTX_KEY`, `build_context()`, `get_app_for_request()`, `session_app()`, cookie/token/BYOK-header helpers, `prune_stale()` |
| `middleware.py` | The middleware stack (`MIDDLEWARES`) |
| `routes_api.py` | `handle_api()`: the `POST /api/{method}` dispatch |
| `routes_auth.py` | `/auth/*`: password sign-in, profile, OAuth |
| `routes_documents.py` | `/api/memory/*`, `/api/documents/*` |
| `routes_images.py` | `/api/images/*` |
| `routes_misc.py` | `/health`, `/api/events` (SSE), static files |
| `request_io.py` | `read_json_object()`, `non_string_field()`, `read_upload()` |
| `constants.py` | Limits, cookie/header names, `IMAGE_SERVE_CSP` |

Handlers are module-level functions; the state they share (the shared app or
`SessionManager`, `AuthManager`, allowed origins, limiter tables) is one
`ServerContext` stored on the aiohttp app, read with `request.app[CTX_KEY]`.
Tests patch a setting in the submodule that reads it (e.g.
`middleware.RATE_LIMIT_MAX`, `routes_auth.LOGIN_RATE_LIMIT_MAX`).

### Single-user vs multi-user

When `multi_user=False` (default), a single `ConsensusApp` is shared by all
clients (suitable for local/desktop use). When `multi_user=True`, each browser
session gets its own `ConsensusApp` via `SessionManager`, identified by a
`consensus_sid` cookie, and `/api/*` requires a signed-in user.

### Middleware stack (applied in order)

| Middleware | Purpose |
|------------|---------|
| `security_headers_middleware` | Adds `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy` |
| `cors_middleware` | Rejects `/api/` requests from an unknown `Origin`. The local origin (`http://host:port`) and `CONSENSUS_ALLOWED_ORIGINS` are allowed |
| `rate_limit_middleware` | Per-peer-IP limit (`RATE_LIMIT_MAX` = 120 per `RATE_LIMIT_WINDOW` = 60 s) on `/api/`; never keyed on a client-supplied cookie |
| `csrf_middleware` | A `POST` to `/api/` or `/auth/` must be `application/json` **or** carry the `X-Consensus-Request` header (`CSRF_HEADER`, sent by the frontend on multipart uploads). A cross-site form can send neither. Excludes `/auth/oauth/callback/` (Apple's `form_post`) |
| `auth_middleware` | In multi-user mode, enforces authentication on `/api/*` and resolves the user for `/auth/me`. Token from `Authorization: Bearer` (wins) or the `consensus_auth` cookie; the user goes in `request["auth_user"]` |

### BYOK request flow

The frontend may send API keys via an `X-API-Keys` header (JSON-encoded map
of `provider_id -> key`). `handle_api`:
1. Extracts keys via `extract_api_keys(request)` (malformed header → none)
2. Sets keys via `app.set_request_api_keys()` before the call
3. Clears keys via `app.clear_request_api_keys()` in a `finally` block

Keys are never logged or persisted.

### API routing

All API calls are `POST /api/{method}` where `{method}` maps to a key in the
`handlers` dict in `handle_api()`, whose lambdas build a `functools.partial`
of a `ConsensusApp` method with arguments (and defaults) from the JSON body.
The call is checked against the method's signature before it runs, so a
missing key, an unconvertible or overflowing number, or a keyword argument a
fixed-signature method does not take is a 400 (`Invalid arguments for
<method>: ...`; a `**kwargs` method such as `update_provider` ignores unknown
keys); an exception raised inside the method is a 500 carrying its message. A body that is not a JSON object is a 400; an
unknown method is a 404. `tests/test_server_api.py` pins every entry's
forwarded arguments, and its `DISPATCH` must name exactly the table's keys.

If a handler returns a coroutine (e.g., `generate_ai_turn`), it is awaited.

**Response format:** Every successful API response wraps the result:
```json
{"result": <return value>, "state": <full app state>}
```

This means the frontend always receives the latest state after any mutation.

The memory/document/image routes and the SSE stream start with
`session_app(request)`, which returns the request's app or the response to
send instead: 401 without a signed-in user, 400 (`NO_SESSION_ERROR`) when
`--multi-user` has no live session for the cookie. They never create a
session (`SessionManager.lookup()`), because they cannot set its cookie.
Uploads go through `read_upload()`, which lifts aiohttp's 1 MiB body limit to
`MAX_DOCUMENT_BYTES` / `MAX_IMAGE_BYTES` for that read only; malformed ids and
bodies are 400s.

### Session cookies

In multi-user mode, every `/api/{method}` reply that has a session (errors
included; the capacity 503 has none) sets a `consensus_sid` cookie (httponly,
SameSite=Lax, `Secure` when `CONSENSUS_ALLOWED_ORIGINS` is set); it is the
only route that creates a session. A malformed incoming id is replaced. The
frontend opens the SSE stream only after its first `get_state` call, so a
first visit's stream joins the session that call created. At the next
keepalive after its session is evicted, the stream sends `session_expired`
(the page shows a notice) and ends. If a dispatch call has already re-created
the session, EventSource's reconnect joins it; otherwise the reconnect gets
the no-session 400 and closes, and the frontend calls `get_state` (creating
the next session) and reopens it, with exponential backoff and a notice after
`SSE_REOPEN_MAX_ATTEMPTS` failures. Keepalives do not extend the session's
TTL. Migrations re-run for a database file deleted by eviction and re-created
at the same path.

### Authentication (multi-user mode)

When `multi_user=True`, `build_context()` creates an `AuthManager` with a
dedicated `auth.db` database. Auth endpoints are registered under `/auth/*`
(separate from `/api/*`).

- **Login rate limiting:** Per-email brute-force protection (5 failed attempts
  per 5-minute window). State is in-memory, resets on restart, and stale
  entries are pruned once per window.
- **Token handling:** Auth tokens are set as httpOnly cookies only — never
  returned in JSON response bodies.
- **Profile updates:** `handle_auth_update_profile` accepts only
  `display_name` and `avatar_url`. Email is never editable (see the
  Authentication doc).
- **OAuth redirect URI:** Derived from `CONSENSUS_BASE_URL` env var (not
  request headers) to prevent host header injection.

For full auth endpoint reference, see [Authentication](11-authentication.md).

### SSE endpoint

`GET /api/events` provides a Server-Sent Events stream for real-time updates:
- Content-Type: `text/event-stream`
- Emits `tool_progress` (MCP/tool progress) and `user_input_request`
  (ask_user) events
- Keepalive comments every `SSE_KEEPALIVE_SECONDS` (30 s)
- Queue-based event dispatch per connected client
- Graceful cleanup on client disconnect

The SSE endpoint subscribes with `ConsensusApp.on(...)` and unsubscribes when
the client goes away.

### Non-API endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/health` | GET | Health check (returns `{"status": "ok"}` or `{"status": "ok", "active_sessions": N}` in multi-user mode) |
| `/api/events` | GET | Server-Sent Events stream for real-time progress updates |
| `/auth/*` | Various | Authentication endpoints (register, login, logout, OAuth). See [Authentication](11-authentication.md) |
| `/eval/` | Various | Evaluation UI — single-user only, never mounted in multi-user mode |

---

## `auth/` -- Authentication

The auth package (`constants`, `models`, `passwords`, `database`, `oauth`,
`manager`) provides email/password registration and OAuth sign-in
(GitHub, Google, LinkedIn, Apple). It is only used in multi-user web mode.

**Key classes:**
- `AuthDatabase` — SQLite persistence for users, tokens, OAuth state, and
  OAuth identities (separate `auth.db` file)
- `AuthManager` — high-level API: register, login, logout, OAuth callback
- `User` — dataclass with `to_dict()` (excludes `password_hash`)

**Security highlights:**
- PBKDF2-SHA256 with 600k iterations for password hashing
- `hmac.compare_digest()` for timing-safe password verification
- `secrets.token_urlsafe(32)` for token generation
- SHA-256 hashed token storage
- Multiple OAuth identities per user via `user_oauth_identities` table

For full details, see [Authentication](11-authentication.md).

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

## `desktop/` -- Desktop Mode (pywebview)

The desktop mode uses pywebview to render the same HTML/CSS/JS in a native
window. The key challenge is bridging pywebview's synchronous JS-to-Python
calls with the async application methods.

**`DesktopBridge`** (`desktop/bridge.py`) is the JS API object passed to
pywebview. Every public method (no leading underscore), inherited ones
included, becomes callable from JavaScript as
`window.pywebview.api.<method_name>(...)`. The class holds the event-loop
plumbing, `get_state` and state pushes, image data URLs and the evaluation
server; the rest come from one mixin per UI area: `catalog.py` (providers,
entities, prompts, tools, MCP servers, experts, memory config),
`discussion.py` (setup, turn lifecycle, history) and `files.py` (documents,
images, export, native file dialogs). pywebview also recurses into every
public class or non-callable attribute, so the bridge keeps its
`ConsensusApp` as `_app`; a public `app` would expose the whole app and its
`Database` to JavaScript (#125). `tests/test_desktop_bridge_api.py` mirrors
that recursion and pins the full exposed set against the frontend's
`pywebview.api` calls.

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
`list_tools()`, `get_entity_tools()`, `assign_tool()`, `remove_tool()`,
`set_tool_override()`. They forward to `ConsensusApp.list_available_tools()`,
`get_entity_tools()`, `assign_tool_to_entity()`, `remove_entity_tool()` and
`set_discussion_tool_override()`.

**MCP/Expert bridge methods:** `DesktopBridge` exposes MCP server and expert
management: `get_mcp_servers()`, `add_mcp_server()`, `update_mcp_server()`,
`delete_mcp_server()`, `test_mcp_connection()`, `save_expert_definition()`,
`get_expert_definitions()`, `consult_expert()`. Async methods (delete, test,
consult) are wrapped in `_run_async()`.

**Document bridge methods:** `upload_document()` (opens a file picker),
`add_document_from_url()`, `get_discussion_documents()`, `remove_document()`,
`delete_document()`.

**Image bridge methods:** `upload_image()` (opens a file picker for
PNG/JPEG/GIF/WebP), `add_image_from_url()`, `get_discussion_images()`,
`remove_image()`, `delete_image()`.

**`launch_desktop()`** (`desktop/launcher.py`) creates the pywebview window (1280x800, min 900x600)
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
