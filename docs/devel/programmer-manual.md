# Consensus -- Programmer's Manual

*For contributors new to the codebase*

---

## Table of Contents

1. [What Is Consensus?](#1-what-is-consensus)
2. [Getting a Dev Environment Running](#2-getting-a-dev-environment-running)
3. [Repository Layout](#3-repository-layout)
4. [Architecture Overview](#4-architecture-overview)
5. [Module-by-Module Guide](#5-module-by-module-guide)
   - 5.1 [models.py -- Data Structures](#51-modelspy--data-structures)
   - 5.2 [config.py -- Paths and API Key Management](#52-configpy--paths-and-api-key-management)
   - 5.3 [database.py -- SQLite Persistence](#53-databasepy--sqlite-persistence)
   - 5.4 [ai_client.py -- LLM Communication](#54-ai_clientpy--llm-communication)
   - 5.5 [moderator.py -- Discussion Logic](#55-moderatorpy--discussion-logic)
   - 5.6 [app.py -- The Orchestrator](#56-apppy--the-orchestrator)
   - 5.7 [server.py -- Web Mode (aiohttp)](#57-serverpy--web-mode-aiohttp)
   - 5.8 [desktop.py -- Desktop Mode (pywebview)](#58-desktoppy--desktop-mode-pywebview)
   - 5.9 [\_\_main\_\_.py -- Entry Point](#59-__main__py--entry-point)
6. [Frontend (HTML / CSS / JS)](#6-frontend-html--css--js)
   - 6.1 [index.html -- Page Structure](#61-indexhtml--page-structure)
   - 6.2 [style.css -- Theming and Layout](#62-stylecss--theming-and-layout)
   - 6.3 [app.js -- Frontend Application](#63-appjs--frontend-application)
7. [Database Schema](#7-database-schema)
8. [Data Flow: A Turn From End to End](#8-data-flow-a-turn-from-end-to-end)
9. [The Discussion Lifecycle](#9-the-discussion-lifecycle)
10. [API Surface Reference](#10-api-surface-reference)
    - 10.1 [REST API (Web Mode)](#101-rest-api-web-mode)
    - 10.2 [pywebview Bridge (Desktop Mode)](#102-pywebview-bridge-desktop-mode)
11. [The Prompt Template System](#11-the-prompt-template-system)
12. [AI Provider Integration](#12-ai-provider-integration)
13. [Security Considerations](#13-security-considerations)
14. [Conventions and Patterns](#14-conventions-and-patterns)
15. [Common Tasks for New Contributors](#15-common-tasks-for-new-contributors)
16. [Current Limitations and Future Work](#16-current-limitations-and-future-work)

---

## 1. What Is Consensus?

Consensus is a **moderated discussion platform** that enables structured
multi-party dialogues between humans and AI entities. A designated moderator
(human or AI) manages the discussion flow: controlling turn order, summarising
each turn, mediating conflicts, and producing a final synthesis when the
discussion concludes.

The application runs in two modes sharing a single backend:

- **Desktop mode** -- a native window via pywebview
- **Web mode** -- an aiohttp HTTP server accessible from any browser

Both modes serve the same vanilla HTML/CSS/JS frontend and route all logic
through the same `ConsensusApp` orchestrator class.

---

## 2. Getting a Dev Environment Running

### Prerequisites

- Python 3.11 or later
- (Optional) [uv](https://docs.astral.sh/uv/) for faster package management
- (Optional) A local Ollama instance for zero-cost AI testing

### Installation

```bash
git clone https://github.com/hherb/consensus.git
cd consensus

# Editable install with all optional dependencies
pip install -e ".[all]"

# Or pick just the mode you need:
pip install -e ".[desktop]"   # pywebview only
pip install -e ".[web]"       # aiohttp only
pip install -e "."            # base (httpx only, no UI server)
```

The base install (`pip install -e "."`) pulls in only `httpx` and
`python-dotenv`. The `desktop` extra adds `pywebview>=5.0`; the `web` extra
adds `aiohttp>=3.9`; `all` includes both.

### Running

```bash
# Desktop mode (default)
python -m consensus

# Web mode
python -m consensus --web
python -m consensus --web --port 9090 --debug

# Via the installed entry point
consensus
consensus --web
```

### Setting up an AI provider for testing

The quickest way to test is with a local Ollama instance (no API key needed).
The application ships with Ollama pre-configured as a default provider. If you
want to use a cloud provider, set the appropriate environment variable:

```bash
export OPENAI_API_KEY="sk-..."
export ANTHROPIC_API_KEY="sk-ant-..."
```

Or use the UI's Providers tab to enter keys, which are saved to
`~/.consensus/.env` with `0600` permissions.

### No test suite yet

There is currently no test suite, linter configuration, or CI pipeline. This
is an area ripe for contribution.

---

## 3. Repository Layout

```
consensus/
  __init__.py          Package marker; defines __version__
  __main__.py          CLI entry point (argparse, mode selection)
  models.py            Dataclasses: Entity, AIConfig, Message, Discussion, ...
  config.py            Platform paths, .env loading, API key helpers
  database.py          SQLite persistence layer (7 tables)
  ai_client.py         Async OpenAI-compatible HTTP client (httpx)
  moderator.py         Turn flow, AI generation, prompt resolution
  app.py               ConsensusApp orchestrator (all business logic)
  server.py            aiohttp web server (REST routes, static files)
  desktop.py           pywebview launcher and JS-Python bridge
  static/
    index.html         Single-page HTML (setup + discussion views)
    style.css          All styling (dark/light themes via CSS custom properties)
    app.js             Entire frontend logic (~1500 lines vanilla JS)
docs/
  plans/               Design documents for specific features
  devel/               Developer documentation (you are here)
pyproject.toml         Build config, dependencies, entry points
CLAUDE.md              Instructions for AI coding assistants
README.md              User-facing project overview
QUICKSTART.md          Quick start guide for end users
```

---

## 4. Architecture Overview

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
     |     Turn flow, prompt resolution, AI generation calls
     |
     +-- AIClient (ai_client.py)
     |     Async HTTP via httpx to any OpenAI-compatible endpoint
     |
     +-- Database (database.py)
           Thread-safe SQLite: providers, entities, prompts,
           discussions, members, messages, storyboard
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

## 5. Module-by-Module Guide

### 5.1 `models.py` -- Data Structures

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
| `Message` | A single message in a discussion | `entity_id`, `entity_name`, `content`, `role`, `timestamp`, plus AI metadata (tokens, latency) |
| `StoryboardEntry` | A moderator summary after a turn | `turn_number`, `summary`, `speaker_name`, `timestamp` |
| `Discussion` | Full in-memory discussion state | `entities`, `messages`, `storyboard`, `turn_order`, `current_turn_index`, `turn_number`, `is_active` |

The `Discussion` class has two important computed properties:

- `moderator` -- returns the `Entity` whose `id` matches `moderator_id`
- `current_speaker` -- returns the entity at `turn_order[current_turn_index]`

**`resolve_api_key(env_var)`** looks up an environment variable name and
returns its value. API keys are *never* stored directly in the database; only
the env var name (e.g. `OPENAI_API_KEY`) is persisted.

### 5.2 `config.py` -- Paths and API Key Management

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

### 5.3 `database.py` -- SQLite Persistence

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

**Tables (7 total):**

| Table | Purpose |
|-------|---------|
| `schema_version` | Tracks DB schema version for migrations |
| `providers` | AI API provider endpoints (name, base_url, api_key_env) |
| `entities` | Participant profiles (name, type, AI config, avatar_color, active) |
| `prompts` | Template prompts (role, target, task, content, is_default) |
| `discussions` | Discussion records (topic, moderator_id, status, timestamps) |
| `discussion_members` | Junction table linking entities to discussions (turn position) |
| `messages` | All messages with AI metadata (model, tokens, latency) |
| `storyboard_entries` | Moderator summaries keyed by turn number |

**Seeding:** On first run, `_seed_default_prompts()` inserts 9 prompt
templates and `_seed_default_providers()` inserts 5 providers (Ollama,
Anthropic, DeepSeek, Mistral, OpenAI). Both seed methods check `COUNT(*)`
first so they only run once.

**Migrations:** The class includes two migration methods:
- `_migrate_providers()` -- fixes a DeepSeek base URL issue and migrates
  literal API keys out of the database into `~/.consensus/.env`
- `_migrate_entity_active()` -- adds the `active` column for entity
  soft-delete

**Entity soft-delete:** Deleting an entity that is referenced by past
discussions raises a foreign key constraint error. The `delete_entity()`
method catches `sqlite3.IntegrityError` and soft-deletes instead (sets
`active=0`). `reactivate_entity()` reverses this.

### 5.4 `ai_client.py` -- LLM Communication

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
```

The `stream()` method is defined but not currently used by the application --
all generation currently uses `complete()`.

### 5.5 `moderator.py` -- Discussion Logic

`Moderator` manages everything related to **discussion flow and AI
generation**. It holds a reference to the current `Discussion` and the
`Database`.

**Prompt resolution:** `resolve_prompt(role, target, task, **variables)` looks
up a prompt template from the database and fills in template variables using
simple string replacement:
```python
template.replace("{entity_name}", str(val))
```

Template variables include: `{entity_name}`, `{topic}`, `{participants}`,
`{speaker_name}`, `{turn_number}`, `{context}`.

**Context building:** `_build_context(system_prompt, task)` constructs the
message list sent to the AI:
1. A `system` message with the system prompt
2. A `user` message containing the discussion topic, the last
   `CONTEXT_MESSAGE_LIMIT` (20) messages, and the task prompt

**AI client caching:** `_get_client(entity)` maintains a dict of `AIClient`
instances keyed by entity ID, so a client is created once per entity and
reused.

**Core methods:**

| Method | Purpose |
|--------|---------|
| `generate_turn(entity)` | Generate an AI participant's contribution |
| `generate_summary()` | Generate a moderator summary after a turn |
| `generate_conclusion()` | Generate a final discussion synthesis |
| `mediate(context)` | Generate a moderator mediation intervention |
| `get_human_guidance(role)` | Return guidance text for a human moderator/participant |
| `advance_turn()` | Increment `current_turn_index`, bump `turn_number`, return next speaker |
| `reassign_turn(entity_id)` | Jump to a specific entity in the turn order |

**Temperature settings:** Participant turns use the entity's configured
temperature. Moderator actions (summary, conclusion, mediation) use a fixed
`MODERATOR_TEMPERATURE = 0.5` for more consistent output.

### 5.6 `app.py` -- The Orchestrator

`ConsensusApp` is the **central controller**. Both the web server and the
desktop bridge delegate to this class. It owns the `Database`, the current
`Discussion`, and the `Moderator`.

**State management:**
```python
class ConsensusApp:
    def __init__(self, db_path=""):
        self.db = Database(db_path or get_db_path())
        self.discussion = Discussion()
        self.moderator = Moderator(self.discussion, self.db)
        self._on_update = None  # callback for push-based state updates
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

**Method groups:**

| Group | Methods |
|-------|---------|
| **Provider management** | `add_provider`, `update_provider`, `delete_provider`, `get_providers`, `fetch_models` |
| **Entity management** | `save_entity`, `delete_entity`, `reactivate_entity`, `get_entities`, `get_inactive_entities` |
| **Prompt management** | `save_prompt`, `delete_prompt`, `get_prompts` |
| **Discussion setup** | `add_to_discussion`, `remove_from_discussion`, `set_moderator`, `set_topic` |
| **Discussion lifecycle** | `start_discussion`, `submit_human_message`, `submit_moderator_message`, `generate_ai_turn`, `complete_turn`, `reassign_turn`, `mediate`, `conclude_discussion` |
| **History** | `load_discussion`, `get_export_data`, `reset` |

**Security:** The `_provider_for_frontend()` static method strips the
`api_key_env` field from provider data before sending it to the frontend,
replacing it with a boolean `has_key` flag.

### 5.7 `server.py` -- Web Mode (aiohttp)

The web server is a single async function `launch_web()` that:

1. Creates a `ConsensusApp` instance
2. Sets up a CORS middleware (restricts API calls to same-origin)
3. Defines a dispatch-style `handle_api()` handler
4. Serves static files with path traversal protection
5. Blocks until interrupted

**API routing:** All API calls are `POST /api/{method}` where `{method}` maps
to a key in a `handlers` dict. The handler dict maps method names to lambdas
that call `ConsensusApp` methods with the appropriate arguments from the JSON
request body.

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

### 5.8 `desktop.py` -- Desktop Mode (pywebview)

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

**`launch_desktop()`** creates the pywebview window (1280x800, min 900x600)
pointing at `static/index.html`, wires up the bridge, and starts the webview
event loop.

### 5.9 `__main__.py` -- Entry Point

Parses command-line arguments (`--web`, `--host`, `--port`, `--debug`),
calls `load_env()` to load `~/.consensus/.env`, and dispatches to either
`launch_web()` or `launch_desktop()`. Prints a helpful error if the required
optional dependency is not installed.

---

## 6. Frontend (HTML / CSS / JS)

The frontend is a **single-page application** built with vanilla JavaScript --
no frameworks, no build step, no npm.

### 6.1 `index.html` -- Page Structure

Two top-level `<section>` elements, toggled with the `.hidden` class:

- **`#setup-phase`** -- shown before a discussion starts. Contains a tab bar
  with 5 tabs:
  - *New Discussion* -- topic input, entity picker, start button
  - *Providers* -- manage API provider endpoints
  - *Profiles* -- manage entity profiles (human/AI)
  - *Prompts* -- edit prompt templates
  - *History* -- browse and load past discussions

- **`#discussion-phase`** -- shown during an active discussion. Three-column
  grid:
  - Left: participant sidebar with speaking indicator
  - Centre: message feed + input area
  - Right: storyboard panel (running summaries)

Five modal dialogs for editing providers, entities, prompts, moderator input,
and turn reassignment.

### 6.2 `style.css` -- Theming and Layout

**Dark/light theme:** CSS custom properties in `:root` define dark mode
colours. A `@media (prefers-color-scheme: light)` block overrides them for
light mode. The theme follows the OS preference automatically.

**Layout:** The discussion phase uses CSS Grid with three columns
(`200px 1fr 260px`). Responsive breakpoint at 900px collapses to a single
column.

**No external dependencies.** All styling is self-contained.

### 6.3 `app.js` -- Frontend Application

The frontend JS (~1500 lines) is structured as follows:

**API Adapters (lines 1-82):** Two classes (`DesktopAPI` and `WebAPI`) provide
the same interface but communicate differently:
- `DesktopAPI` calls `window.pywebview.api.<method>()` (synchronous Python bridge)
- `WebAPI` uses `fetch('/api/<method>', ...)` (HTTP POST)

The correct adapter is selected at startup:
```javascript
api = window.pywebview ? new DesktopAPI() : new WebAPI();
```

**Global state (lines 88-97):** A single `state` object holds the full
application state, updated by `onStateUpdate(newState)`.

**`onStateUpdate(newState)` -- the state refresh function:** Called whenever
the backend pushes new state (desktop mode) or after each API response (web
mode). It merges the new state, re-renders all UI panels, and manages the
setup/discussion phase transitions.

**Key rendering functions:**

| Function | Renders |
|----------|---------|
| `renderProviders()` | Provider list in the Providers tab |
| `renderProfiles()` | Entity profile list in the Profiles tab |
| `renderPrompts()` | Prompt template list in the Prompts tab |
| `renderHistory()` | Discussion history list in the History tab |
| `renderAvailableEntities()` | Selectable entity list for discussion setup |
| `renderDiscussionRoster()` | Entities added to the current discussion |
| `renderDiscussionEntities()` | Participant sidebar during discussion |
| `renderMessages()` | Message feed (incremental -- only new messages) |
| `renderStoryboard()` | Storyboard panel (incremental) |

**Discussion automation:** When the current speaker is AI, the frontend
automatically calls `api.generateAiTurn()` followed by `api.completeTurn()`.
This is handled in `runTurnCycle()`, which loops automatically for consecutive
AI speakers.

**Markdown rendering:** `renderMarkdown()` converts a subset of Markdown to
HTML (headers, bold, italic, code blocks, lists). HTML is escaped first to
prevent XSS.

**Export:** The frontend handles JSON, HTML, and PDF export. JSON and HTML
exports use data fetched via `api.getExportData()`. PDF export opens a print
dialog (via `window.print()` in web mode, or directly in desktop mode).

---

## 7. Database Schema

```sql
-- Version tracking
schema_version (version INTEGER)

-- AI API providers (e.g. OpenAI, Ollama, Anthropic)
providers (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    base_url    TEXT NOT NULL,
    api_key_env TEXT NOT NULL DEFAULT '',   -- env var name, NOT the key
    created_at  REAL NOT NULL
)

-- Participant profiles
entities (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    entity_type     TEXT NOT NULL CHECK('human','ai'),
    avatar_color    TEXT NOT NULL DEFAULT '#3b82f6',
    provider_id     INTEGER REFERENCES providers(id) ON DELETE SET NULL,
    model           TEXT,
    temperature     REAL DEFAULT 0.7,
    max_tokens      INTEGER DEFAULT 1024,
    system_prompt   TEXT DEFAULT '',
    active          INTEGER NOT NULL DEFAULT 1,  -- soft-delete flag
    created_at      REAL NOT NULL,
    updated_at      REAL NOT NULL
)

-- Customisable prompt templates
prompts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    role        TEXT NOT NULL CHECK('moderator','participant'),
    target      TEXT NOT NULL CHECK('ai','human'),
    task        TEXT NOT NULL,  -- e.g. system, turn, summarize, mediate, conclude, open, guidance
    content     TEXT NOT NULL,
    is_default  INTEGER NOT NULL DEFAULT 0,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
)

-- Discussion records
discussions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    topic           TEXT NOT NULL,
    moderator_id    INTEGER REFERENCES entities(id),
    started_at      REAL,
    ended_at        REAL,
    status          TEXT NOT NULL DEFAULT 'setup' CHECK('setup','active','concluded')
)

-- Many-to-many: which entities participate in which discussion
discussion_members (
    discussion_id    INTEGER REFERENCES discussions(id),
    entity_id        INTEGER REFERENCES entities(id),
    is_moderator     INTEGER NOT NULL DEFAULT 0,
    also_participant INTEGER NOT NULL DEFAULT 0,
    turn_position    INTEGER,           -- NULL = not in turn rotation
    PRIMARY KEY (discussion_id, entity_id)
)

-- All messages
messages (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    discussion_id     INTEGER REFERENCES discussions(id),
    entity_id         INTEGER REFERENCES entities(id),
    content           TEXT NOT NULL,
    role              TEXT NOT NULL CHECK('participant','moderator','system'),
    turn_number       INTEGER,
    timestamp         REAL NOT NULL,
    -- AI metadata (NULL for human messages)
    model_used        TEXT,
    prompt_tokens     INTEGER,
    completion_tokens INTEGER,
    total_tokens      INTEGER,
    latency_ms        INTEGER,
    temperature_used  REAL,
    prompt_id         INTEGER
)

-- Moderator summaries, indexed by turn
storyboard_entries (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    discussion_id       INTEGER REFERENCES discussions(id),
    turn_number         INTEGER NOT NULL,
    summary             TEXT NOT NULL,
    speaker_entity_id   INTEGER REFERENCES entities(id),
    timestamp           REAL NOT NULL
)
```

All timestamps are Unix epoch floats (`time.time()`).

---

## 8. Data Flow: A Turn From End to End

Here is what happens when an AI participant takes a turn in web mode:

```
1. Frontend (app.js)
   runTurnCycle() detects current speaker is AI
       |
       v
2. Frontend calls api.generateAiTurn()
   WebAPI._post('generate_ai_turn', {})
       |
       v
3. server.py: handle_api() dispatches to
   app.generate_ai_turn()
       |
       v
4. app.py: ConsensusApp.generate_ai_turn()
   - Checks current_speaker is AI
   - Calls self.moderator.generate_turn(entity)
       |
       v
5. moderator.py: Moderator.generate_turn(entity)
   - Resolves the system prompt (from DB or entity config)
   - Resolves the turn prompt
   - Builds context messages (system + last 20 messages + task)
   - Gets/creates an AIClient for this entity
   - Calls client.complete(messages, model, temperature, max_tokens)
       |
       v
6. ai_client.py: AIClient.complete()
   - POST to {base_url}/chat/completions
   - Returns AIResponse(content, model, tokens, latency)
       |
       v
7. Back in app.py:
   - Creates a Message dataclass from the AIResponse
   - Appends to self.discussion.messages (in-memory)
   - Persists to DB via self.db.add_message(...)
   - Calls self._notify() which triggers state push
   - Returns the message dict
       |
       v
8. server.py: wraps result + full state in JSON response
       |
       v
9. Frontend receives response:
   - onStateUpdate(json.state) re-renders messages
   - Proceeds to call api.completeTurn()
       |
       v
10. app.py: ConsensusApp.complete_turn()
    - If moderator is AI: generates summary via moderator.generate_summary()
    - Stores summary as a message + storyboard entry
    - Calls moderator.advance_turn() to move to next speaker
    - Returns next speaker info + full state
       |
       v
11. Frontend: if next speaker is also AI, loops back to step 1
```

In desktop mode, steps 2-8 go through `DesktopBridge._run_async()` instead of
HTTP, but the `ConsensusApp` logic is identical.

---

## 9. The Discussion Lifecycle

A discussion moves through three states:

```
   setup  -->  active  -->  concluded
```

### Setup Phase

1. User creates entity profiles (Profiles tab) and configures providers
   (Providers tab)
2. User enters a topic
3. User adds entities to the discussion, designates one as moderator
4. User clicks "Start Discussion"

`start_discussion()`:
- Validates: topic set, >=2 participants, moderator designated
- Creates a DB `discussions` record (status=`active`)
- Builds the turn order (all participants except moderator, unless moderator
  also participates)
- Stores `discussion_members` records with turn positions
- Generates the moderator's opening message (from the "open" prompt template)
- Sets `discussion.is_active = True`

### Active Phase

The discussion runs in turns:
1. The current speaker takes their turn (human types, AI generates)
2. After each turn, the moderator provides a summary (AI generates, or human
   types)
3. The summary is stored as a storyboard entry
4. Turn advances to the next speaker in order

Special actions during the active phase:
- **Reassign turn** -- jump to a different speaker
- **Mediate** -- moderator intervenes (AI generates mediation text, or human
  types)
- **Conclude** -- end the discussion

### Concluded Phase

`conclude_discussion()`:
- If the moderator is AI, generates a final synthesis (conclusion prompt)
- Stores the conclusion as a message and storyboard entry
- Sets `discussion.is_active = False`
- Updates the DB record to `status='concluded'`, sets `ended_at`

### Loading Past Discussions

`load_discussion(discussion_id)` reconstructs the full `Discussion` object
from DB rows (members, messages, storyboard, turn order) and replaces the
current in-memory state. If the discussion was still active, it can be
continued.

### Resetting

`reset()` replaces `self.discussion` with a fresh `Discussion()` and creates
a new `Moderator`. This returns to the setup phase in the UI.

---

## 10. API Surface Reference

### 10.1 REST API (Web Mode)

All endpoints: `POST /api/{method}` with JSON body.

| Method | JSON body fields | Returns |
|--------|-----------------|---------|
| `get_state` | *(none)* | Full application state |
| **Providers** | | |
| `add_provider` | `name`, `base_url`, `api_key_env?`, `api_key?` | Provider dict |
| `update_provider` | `provider_id`, `name?`, `base_url?`, `api_key_env?`, `api_key?` | `true` |
| `delete_provider` | `provider_id` | `true` |
| `fetch_models` | `provider_id` | List of model ID strings |
| **Entities** | | |
| `save_entity` | `name`, `entity_type`, `avatar_color?`, `provider_id?`, `model?`, `temperature?`, `max_tokens?`, `system_prompt?`, `entity_id?` | Entity dict |
| `delete_entity` | `entity_id` | `{"deleted": true}` or `{"deactivated": true}` |
| `reactivate_entity` | `entity_id` | `true` |
| `get_inactive_entities` | *(none)* | List of entity dicts |
| **Prompts** | | |
| `save_prompt` | `prompt_id?`, `name`, `role`, `target`, `task`, `content` | Prompt dict |
| `delete_prompt` | `prompt_id` | `true` |
| **Discussion Setup** | | |
| `add_to_discussion` | `entity_id`, `is_moderator?`, `also_participant?` | Entity dict |
| `remove_from_discussion` | `entity_id` | `true` |
| `set_moderator` | `entity_id`, `also_participant?` | `true` |
| `set_topic` | `topic` | `true` |
| **Discussion Lifecycle** | | |
| `start_discussion` | `moderator_participates?` | Full state |
| `submit_human_message` | `entity_id`, `content` | Message dict |
| `submit_moderator_message` | `content` | Message dict |
| `generate_ai_turn` | *(none)* | Message dict or `{"error": ...}` |
| `complete_turn` | `moderator_summary?` | `{next_speaker, turn_number, state}` |
| `reassign_turn` | `entity_id` | `{reassigned_to, state}` |
| `mediate` | `context?` | Message dict |
| `conclude` | *(none)* | Full state |
| **History / Export** | | |
| `get_export_data` | `discussion_id` | Discussion dict (read-only) |
| `load_discussion` | `discussion_id` | Full state |
| `reset` | *(none)* | `true` |

Every response is wrapped: `{"result": <value>, "state": <full state>}`

### 10.2 pywebview Bridge (Desktop Mode)

The `DesktopBridge` class mirrors the same methods. From JavaScript:

```javascript
const result = await window.pywebview.api.generate_ai_turn();
const state  = await window.pywebview.api.get_state();
```

The method names and parameters match the REST API. The bridge handles
sync-to-async conversion internally.

---

## 11. The Prompt Template System

Prompts are stored in the `prompts` table and categorised by three axes:

| Axis | Values | Meaning |
|------|--------|---------|
| `role` | `moderator`, `participant` | Who uses this prompt |
| `target` | `ai`, `human` | Is the user AI or human? |
| `task` | `system`, `turn`, `summarize`, `mediate`, `conclude`, `open`, `guidance` | What the prompt is for |

**Default prompts seeded on first run (9 total):**

| Name | Role | Target | Task |
|------|------|--------|------|
| AI Moderator -- System | moderator | ai | system |
| AI Moderator -- Summarize | moderator | ai | summarize |
| AI Moderator -- Mediate | moderator | ai | mediate |
| AI Moderator -- Conclude | moderator | ai | conclude |
| AI Moderator -- Open | moderator | ai | open |
| AI Participant -- System | participant | ai | system |
| AI Participant -- Turn | participant | ai | turn |
| Human Moderator -- Guidance | moderator | human | guidance |
| Human Participant -- Guidance | participant | human | guidance |

**Template variables** (replaced by `Moderator.resolve_prompt()`):

| Variable | Replaced with |
|----------|--------------|
| `{entity_name}` | The speaking entity's name |
| `{topic}` | The discussion topic |
| `{participants}` | Comma-separated list: "Alice (Human), GPT-4 (AI)" |
| `{speaker_name}` | The entity who just spoke (for summaries) |
| `{turn_number}` | Current turn number |
| `{context}` | Additional context (for mediation) |

**Prompt priority:** If an entity has a custom `system_prompt` set in its
profile, that overrides the database template for the system prompt. Task
prompts (turn, summarize, etc.) always come from the database.

**How prompts are selected:** `Moderator.resolve_prompt(role, target, task)`
calls `Database.get_prompt_by_task()`, which returns the first match ordered
by `is_default DESC`. Default prompts are preferred but user-created prompts
for the same role/target/task combination will be used if they exist.

---

## 12. AI Provider Integration

The system is designed around the **OpenAI chat completions API** as a common
protocol. Any LLM server that implements `/v1/chat/completions` and
`/v1/models` works.

### Provider registry

Providers are stored in the `providers` table:
```
id | name            | base_url                      | api_key_env
---+-----------------+-------------------------------+------------------
1  | Ollama (Local)  | http://localhost:11434/v1      |
2  | Anthropic       | https://api.anthropic.com/v1   | ANTHROPIC_API_KEY
3  | OpenAI          | https://api.openai.com/v1      | OPENAI_API_KEY
4  | DeepSeek        | https://api.deepseek.com       | DEEPSEEK_API_KEY
5  | Mistral         | https://api.mistral.ai/v1      | MISTRAL_API_KEY
```

### How an entity connects to a provider

1. Entity profile has a `provider_id` (foreign key to `providers`)
2. When loading an entity, `get_entities()` does a `LEFT JOIN` with
   `providers` to include `base_url` and `api_key_env`
3. `AIConfig.from_db_row()` builds the config, calling
   `resolve_api_key(api_key_env)` to look up the actual key from `os.environ`
4. `Moderator._get_client(entity)` creates an `AIClient(base_url, api_key)`
5. `AIClient.complete()` posts to `{base_url}/chat/completions`

### Anthropic special handling

Anthropic's native API uses `x-api-key` header instead of `Authorization:
Bearer`. The `AIClient` detects Anthropic URLs and switches to a separate
`_list_models_anthropic()` method with the correct headers and pagination.

> **Note:** For chat completions, Anthropic is expected to be accessed through
> their OpenAI-compatible proxy, which does use Bearer auth. The special
> handling is only for model listing.

### Adding a new provider type

If a new provider needs special handling beyond the OpenAI-compatible protocol:
1. Add detection logic in `AIClient` (like `_is_anthropic()`)
2. Override the relevant method (`list_models`, `complete`, or `stream`)
3. The rest of the system (prompts, turns, summaries) works unchanged

---

## 13. Security Considerations

These are the security measures currently in place. Contributors should
maintain these invariants:

- **API keys are never stored in the database.** Only environment variable
  names (e.g. `OPENAI_API_KEY`) are persisted. Actual keys live in
  `~/.consensus/.env` with `0600` permissions, or in the process environment.

- **Keys are never sent to the frontend.** `_provider_for_frontend()` strips
  the `api_key_env` field and replaces it with a boolean `has_key`.

- **Path traversal protection:** `server.py` resolves static file paths with
  `os.path.realpath()` and checks they start with the static directory prefix.

- **CORS origin checking:** The web server middleware rejects API requests from
  non-matching `Origin` headers.

- **HTML escaping:** `renderMarkdown()` in `app.js` escapes HTML entities
  before applying Markdown formatting, preventing XSS from message content.

- **SQL injection prevention:** All database queries use parameterised
  statements (`?` placeholders). The one dynamic table name in `_update_row()`
  is validated against a whitelist (`_VALID_TABLES`).

---

## 14. Conventions and Patterns

### Python

- **Dataclasses for data, classes for behaviour.** Domain objects in
  `models.py` are pure data. Behaviour lives in `Moderator`, `ConsensusApp`,
  etc.
- **Async by default.** All AI-calling and HTTP code is async. Desktop mode
  bridges async to sync with `run_coroutine_threadsafe`.
- **Error returns over exceptions.** Most `ConsensusApp` methods return
  `{"error": "..."}` dicts rather than raising exceptions. Exceptions from
  the AI layer are caught and converted to error dicts.
- **Thread safety via lock.** Only one write lock in `Database`. Reads are
  lock-free (SQLite WAL handles concurrent reads).
- **No type annotations on `kwargs`.** The codebase uses `**kwargs: object` for
  generic update methods.

### JavaScript

- **No framework, no build step.** The entire frontend is one JS file.
- **`$` and `$$` helpers.** `$('#id')` is `document.querySelector`,
  `$$('.class')` is `querySelectorAll`.
- **Incremental rendering.** Messages and storyboard entries track how many
  have been rendered and only add new ones. Full re-renders happen for setup
  panels.
- **API adapter pattern.** `DesktopAPI` and `WebAPI` provide the same
  interface, selected at startup based on `window.pywebview` detection.

### State flow

```
Backend mutation
    --> app.get_state() builds full state dict
    --> Sent to frontend (HTTP response or evaluate_js)
    --> onStateUpdate(newState) in app.js
    --> Selective UI re-rendering
```

The frontend never has partial state. Every update receives the entire
application state.

---

## 15. Common Tasks for New Contributors

### Adding a new backend feature

1. Add the business logic method to `ConsensusApp` in `app.py`
2. Expose it in **both**:
   - `server.py` -- add an entry to the `handlers` dict in `handle_api()`
   - `desktop.py` -- add a method to `DesktopBridge` (use `_run_async()` if
     async)
3. If it needs new UI, add the corresponding call to the API adapter classes
   (`DesktopAPI` and `WebAPI`) in `app.js`

### Adding a new database table or column

1. Add the `CREATE TABLE` or `ALTER TABLE` statement to `_create_tables()` or
   a new `_migrate_*()` method in `database.py`
2. Add CRUD methods to `Database`
3. Expose through `ConsensusApp`, `server.py`, and `desktop.py` as needed

### Adding a new prompt template

Add it to the `defaults` list in `Database._seed_default_prompts()`. Note that
seeds only run when the prompts table is empty (fresh database), so existing
databases won't get new defaults automatically. Consider adding a migration
for existing users.

### Modifying the frontend

- Edit `static/app.js` for logic changes
- Edit `static/style.css` for styling changes
- Edit `static/index.html` for structural changes
- No build step needed; just refresh the browser / restart the app

### Debugging tips

- **Web mode with `--debug`:** Not currently wired to aiohttp's debug mode,
  but you can add `logging.basicConfig(level=logging.DEBUG)` to `__main__.py`
- **Desktop mode with `--debug`:** Passes `debug=True` to `webview.start()`,
  which enables browser developer tools in the webview
- **Database inspection:** The SQLite file is at the path returned by
  `config.get_db_path()`. Open it with `sqlite3` or any SQLite browser.
- **AI request debugging:** `AIClient` uses Python's `logging` module with
  `logger.debug()` calls for failed requests

---

## 16. Current Limitations and Future Work

These are known gaps that represent good contribution opportunities:

- **No test suite.** No unit tests, integration tests, or end-to-end tests
  exist. The project would benefit from pytest-based tests for the backend
  modules.
- **No linter or formatter configuration.** No ruff, black, flake8, or mypy
  config.
- **No CI/CD pipeline.** No GitHub Actions or similar.
- **No streaming responses.** `AIClient.stream()` exists but is unused. The
  frontend doesn't handle streaming display.
- **Single-user only.** The web server holds a single `ConsensusApp` instance
  in memory. Multiple browser tabs share state, and there's no concept of
  user sessions.
- **No WebSocket for real-time updates.** In web mode, the frontend only
  receives state updates via HTTP response bodies. There's no push channel
  (the desktop mode does have push via `evaluate_js`).
- **No authentication or authorisation.** The web server is intended for local
  use or trusted networks only.
- **Markdown rendering is basic.** The `renderMarkdown()` function handles
  common cases but doesn't cover the full CommonMark spec.
- **No internationalisation.** All UI text is hardcoded in English.

---

*This manual reflects the codebase as of March 2026. If you find it out of
date, please update it as part of your contribution.*
