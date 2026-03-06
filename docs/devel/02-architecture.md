# 2. Architecture

[Back to index](programmer-manual.md) | [Previous: Getting Started](01-getting-started.md) | [Next: Backend Modules](03-backend-modules.md)

---

## High-Level Architecture

```
Frontend (static/index.html + app.js + style.css)
    |
    | pywebview JS bridge       OR       aiohttp REST POST /api/{method}
    | (desktop.py:DesktopBridge)          (server.py:handle_api)
    |
    v
ConsensusApp (app.py)
    |  Central orchestrator: state management, validation, DB writes
    |
    +-- Moderator (moderator.py)
    |     Turn flow, prompt resolution, AI generation, tool execution loop
    |
    +-- ToolRegistry (tools.py)
    |     Pluggable tool providers, access control, execution with timeout
    |
    +-- AIClient (ai_client.py)
    |     Async HTTP via httpx to any OpenAI-compatible endpoint
    |
    +-- Database (database.py)
          Thread-safe SQLite: providers, entities, prompts,
          discussions, members, messages, storyboard, tools
```

**Key principle:** Both UI modes (desktop and web) funnel everything through
`ConsensusApp`. If you are adding backend functionality, you only need to add
it in `app.py` (and expose it through both `server.py` and `desktop.py`).

### Data ownership

- **In-memory state:** `ConsensusApp.discussion` (a `Discussion` dataclass)
  holds the current working copy of the active discussion -- entities, messages,
  storyboard, turn order, etc.
- **Persistent state:** Everything that should survive restarts lives in the
  SQLite database. The `Discussion` object is rebuilt from DB rows when loading
  a past discussion.

---

## Module-by-Module Guide (Core)

### `models.py` -- Data Structures

All domain objects are plain Python `dataclass`es with no framework
dependencies. Every dataclass provides:

- `to_dict()` -- serialise to a plain `dict` (for JSON transport to the
  frontend)
- `from_db_row(row: dict)` -- class method to construct from a SQLite row

**`EntityType`** and **`MessageRole`** are `Enum` classes:
```python
class EntityType(Enum):
    HUMAN = "human"
    AI = "ai"

class MessageRole(Enum):
    PARTICIPANT = "participant"
    MODERATOR = "moderator"
    SYSTEM = "system"
```

**Core dataclasses:**

| Class | Purpose | Key fields |
|-------|---------|------------|
| `AIConfig` | AI model settings for an entity | `base_url`, `api_key`, `model`, `temperature`, `max_tokens`, `system_prompt`, `provider_id` |
| `Entity` | A discussion participant | `name`, `entity_type`, `ai_config`, `id`, `avatar_color` |
| `Message` | A single message in a discussion | `entity_id`, `entity_name`, `content`, `role`, `timestamp`, plus AI metadata (tokens, latency), `tool_calls_json` |
| `StoryboardEntry` | A moderator summary after a turn | `turn_number`, `summary`, `speaker_name`, `timestamp` |
| `Discussion` | Full in-memory discussion state | `entities`, `messages`, `storyboard`, `turn_order`, `current_turn_index`, `turn_number`, `is_active` |

The `Discussion` class has two important computed properties:

- `moderator` -- returns the `Entity` whose `id` matches `moderator_id`
- `current_speaker` -- returns the entity at `turn_order[current_turn_index]`

**`resolve_api_key(env_var)`** looks up an environment variable name and
returns its value. API keys are *never* stored directly in the database; only
the env var name (e.g. `OPENAI_API_KEY`) is persisted.

**Tool call support:** The `Message` dataclass includes a `tool_calls_json`
field (default `""`) that stores a JSON-serialised list of `ToolCallRecord`
dicts. `to_dict()` parses this and includes a `tool_calls` array in the
output.

---

### `config.py` -- Paths and API Key Management

Handles platform-specific paths and secure API key storage.

**Platform data directories:**
- macOS: `~/Library/Application Support/consensus/`
- Linux: `$XDG_DATA_HOME/consensus/` (default: `~/.local/share/consensus/`)
- Windows: `%APPDATA%/consensus/`

**Key functions:**

| Function | Purpose |
|----------|---------|
| `get_data_dir()` | Returns the platform data directory (creates if needed) |
| `get_db_path()` | Returns `<data_dir>/consensus.db` |
| `get_env_path()` | Returns `~/.consensus/.env` |
| `load_env()` | Loads `~/.consensus/.env` into `os.environ` using `python-dotenv` |
| `save_api_key(env_var, key_value)` | Writes/updates a key in `~/.consensus/.env` and sets it in `os.environ` |
| `remove_api_key(env_var)` | Removes a key from `.env` and `os.environ` |
| `has_api_key(env_var)` | Checks whether the env var is currently set |

The `.env` file is written with `0600` permissions (owner read/write only).

---

### `database.py` -- SQLite Persistence

`Database` is a **thread-safe SQLite wrapper**. All write operations are
serialised through a `threading.Lock` to prevent concurrent write failures
when called from pywebview's JS bridge threads.

**Construction:** Opens (or creates) the database, enables WAL mode and
foreign keys, creates all tables, seeds default data, and runs migrations.

```python
db = Database("/path/to/consensus.db")
```

**Write serialisation pattern:**
```python
def _execute_write(self, sql, params=()):
    with self._lock:
        cur = self.conn.execute(sql, params)
        self.conn.commit()
        return cur
```

Every public write method (`add_entity`, `add_message`, etc.) goes through
`_execute_write`. Read methods access `self.conn` directly (SQLite WAL mode
allows concurrent readers).

For full schema details, see [Database](05-database.md).

---

### `ai_client.py` -- LLM Communication

`AIClient` is an async HTTP client that speaks the **OpenAI chat completions
API** using `httpx.AsyncClient`.

**Key design decisions:**
- The client lazily creates and reuses a single `httpx.AsyncClient` for
  connection pooling
- It works as an async context manager (`async with AIClient(...) as client:`)
- Anthropic's API uses a different auth header (`x-api-key`) and model listing
  format, handled by `_is_anthropic()` and `_list_models_anthropic()`
- Default timeout is 120 seconds

**Methods:**

| Method | Purpose |
|--------|---------|
| `list_models()` | GET `{base_url}/models`, returns sorted list of model IDs |
| `complete(messages, model, temperature, max_tokens)` | POST to `/chat/completions`, returns `AIResponse` with content + usage metadata |
| `complete_with_tools(messages, model, tools, temperature, max_tokens)` | POST with tool definitions, returns raw message dict with `tool_calls` array |
| `stream(messages, model, temperature, max_tokens)` | Streaming POST, yields content chunks via Server-Sent Events (SSE) |

**`AIResponse`** is a dataclass bundling the response text with token counts
and latency:
```python
@dataclass
class AIResponse:
    content: str
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    latency_ms: int = 0
    tool_calls: list = field(default_factory=list)
```

The `complete_with_tools()` method returns a dict (not `AIResponse`) containing
the full message object from the API, including any `tool_calls` array, plus
metadata (`finish_reason`, `model`, token counts, `latency_ms`). This is used
by the moderator's tool execution loop.

The `stream()` method is defined but not currently used by the application --
all generation currently uses `complete()` or `complete_with_tools()`.

---

### `moderator.py` -- Discussion Logic

`Moderator` manages everything related to **discussion flow and AI
generation**. It holds a reference to the current `Discussion`, the
`Database`, and optionally a `ToolRegistry`.

**Prompt resolution:** `resolve_prompt(role, target, task, **variables)` looks
up a prompt template from the database and fills in template variables using
simple string replacement:
```python
template.replace("{entity_name}", str(val))
```

Template variables include: `{entity_name}`, `{topic}`, `{participants}`,
`{speaker_name}`, `{turn_number}`, `{context}`.

**Context building:** `_build_context(system_prompt, task)` constructs the
message list sent to the AI in OpenAI message format:
1. A `system` message with the system prompt
2. Discussion history messages (last `CONTEXT_MESSAGE_LIMIT` = 20)
3. A `user` message containing the task prompt

**AI client caching:** `_get_client(entity)` maintains a dict of `AIClient`
instances keyed by entity ID, so a client is created once per entity and
reused.

**Core methods:**

| Method | Purpose |
|--------|---------|
| `generate_turn(entity)` | Generate an AI participant's contribution (with tool execution loop) |
| `generate_summary()` | Generate a moderator summary after a turn |
| `generate_conclusion()` | Generate a final discussion synthesis |
| `mediate(context)` | Generate a moderator mediation intervention |
| `get_human_guidance(role)` | Return guidance text for a human moderator/participant |
| `advance_turn()` | Increment `current_turn_index`, bump `turn_number`, return next speaker |
| `reassign_turn(entity_id)` | Jump to a specific entity in the turn order |

**Tool execution loop (in `generate_turn()`):**

When a `ToolRegistry` is available, `generate_turn()` enters an iterative loop:
1. Gets tools available to the entity via `ToolRegistry.get_tools_for_entity()`
2. Converts tool definitions to OpenAI function-calling schemas
3. Calls `complete_with_tools()` with tool definitions
4. If the model returns `tool_calls`:
   - Executes each tool via `ToolRegistry.execute()`
   - Records a `ToolCallRecord` per call (name, arguments, result, latency)
   - Appends tool results to the message context
   - Loops back (up to `MAX_TOOL_ITERATIONS` = 5)
5. On the final iteration, tools are removed to force a text response
6. All `ToolCallRecord` objects are collected in the returned `AIResponse`

**Temperature settings:** Participant turns use the entity's configured
temperature. Moderator actions (summary, conclusion, mediation) use a fixed
`MODERATOR_TEMPERATURE = 0.5` for more consistent output.

---

[Next: Backend Modules](03-backend-modules.md)
