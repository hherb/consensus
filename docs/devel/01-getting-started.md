# 1. Getting Started

[Back to index](programmer-manual.md)

---

## What Is Consensus?

Consensus is a **moderated discussion platform** that enables structured
multi-party dialogues between humans and AI entities. A designated moderator
(human or AI) manages the discussion flow: controlling turn order, summarising
each turn, mediating conflicts, and producing a final synthesis when the
discussion concludes.

The application runs in three modes sharing a single backend:

- **Desktop mode** -- a native window via pywebview
- **Web mode** -- an aiohttp HTTP server accessible from any browser (single-user)
- **Multi-user mode** -- web mode with per-session isolation, BYOK API keys,
  rate limiting, and security hardening (for public deployment)

All modes serve the same vanilla HTML/CSS/JS frontend and route all logic
through the same `ConsensusApp` orchestrator class.

---

## Dev Environment Setup

### Prerequisites

- Python 3.11 or later
- [uv](https://docs.astral.sh/uv/) (recommended) or pip for package management
- (Optional) A local Ollama instance for zero-cost AI testing

### Installation

```bash
git clone https://github.com/hherb/consensus.git
cd consensus

# Editable install — all features are included by default
uv pip install -e .
# or: uv tool install -e .     # editable global command
```

All features (desktop, web, documents, memory, images) are installed by
default — `pywebview`, `aiohttp`, and the rest are all regular dependencies
now. The old extras (`[all]`, `[desktop]`, `[web]`, …) still parse but are
empty aliases kept only for backward compatibility.

### Running

```bash
# Desktop mode (default)
python -m consensus

# Web mode
python -m consensus --web
python -m consensus --web --port 9090 --debug

# Multi-user mode (public deployment)
python -m consensus --web --multi-user
python -m consensus --web --multi-user --host 0.0.0.0 --port 8080

# Via the installed entry point
consensus
consensus --web
consensus --web --multi-user
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

### Tests

Initial pytest-based tests exist in the `tests/` directory covering `app.py`,
`database.py`, `tools.py`, and `mcp_client.py` (including MCP integration
tests). Coverage is still limited — contributions welcome. No linter
configuration or CI pipeline exists yet.

---

## Repository Layout

```
consensus/
  __init__.py              Package marker; defines __version__
  __main__.py              CLI entry point (argparse, mode selection)
  models.py                Dataclasses: Entity, AIConfig, Message, Discussion, ...
  config.py                Platform paths, .env loading, API key helpers
  ai_client.py             Async OpenAI-compatible HTTP client (httpx)
  moderator.py             Turn flow, AI generation, prompt resolution, tool execution
  app.py                   ConsensusApp orchestrator (central controller)
  app_providers.py         Provider management (extracted from app.py)
  app_entities.py          Entity CRUD (extracted from app.py)
  app_discussion_setup.py  Discussion creation & configuration
  app_discussion_flow.py   Turn flow operations
  app_discussion_state.py  Discussion state management
  server.py                aiohttp web server (REST + resource routes, middleware)
  session.py               Multi-user session manager (per-session app + SQLite)
  auth.py                  Authentication (email/password, OAuth)
  desktop.py               pywebview launcher and JS-Python bridge
  tools.py                 Pluggable tool framework (ToolProvider, ToolRegistry)
  tools_builtin.py         Built-in web search tool (Brave + DuckDuckGo fallback)
  tools_document.py        Document RAG tool provider (ingestion, chunking, Q&A)
  tools_image.py           Image tool provider (storage, vision, multimodal context)
  tools_memory.py          Institutional memory tools (sqlite-vec, Ollama)
  mcp_client.py            MCPToolProvider — JSON-RPC 2.0 communication with MCP servers
  pricing.py               PricingCache — model cost lookup via OpenRouter
  methods.py               Pluggable discussion methods framework
  migrator.py              Auto-discovers and applies numbered SQL migrations
  db/                      Database subpackage with domain-specific mixins:
    __init__.py               Database class (composes all mixins)
    providers.py              Provider CRUD
    entities.py               Entity CRUD
    discussions.py            Discussion CRUD
    messages.py               Message CRUD
    prompts.py                Prompt CRUD
    tools.py                  Tool provider/assignment CRUD
    mcp.py                    MCP server/expert CRUD
    memory.py                 Memory tables CRUD
    documents.py              Document/chunk CRUD
    images.py                 Image storage/association CRUD
  migrations/              Numbered SQL migrations (auto-discovered by migrator.py)
  static/                  Frontend — vanilla JS ES modules
    index.html               Single-page HTML (setup + discussion views)
    style.css                All styling (dark/light themes via CSS custom properties)
    app.js                   Application entry point and event wiring
    api.js                   DesktopAPI / WebAPI adapter classes
    state.js                 Global state management
    utils.js                 DOM helpers, escaping, markdown rendering
    setup.js                 New Discussion tab rendering
    providers.js             Providers tab
    profiles.js              Profiles tab (entity editor, tool assignment)
    prompts.js               Prompts tab
    history.js               History tab
    discussion.js            Active discussion view (messages, storyboard)
    discussion-actions.js    Discussion control actions
    documents.js             Document panel (upload, URL, list)
    images.js                Image panel (upload, URL, grid, lightbox)
    experts.js               MCP expert consultation UI
    mcp.js                   MCP server management UI
    memory.js                Memory configuration UI
    export.js                JSON/HTML/PDF export
    auth.js                  Authentication UI (login/register/OAuth)
    byok.js                  BYOK key management UI
docs/
  plans/                   Design documents for specific features
  devel/                   Developer documentation (you are here)
pyproject.toml             Build config, dependencies, entry points
CLAUDE.md                  Instructions for AI coding assistants
README.md                  User-facing project overview
QUICKSTART.md              Quick start guide for end users
DEPLOYMENT.md              Oracle Cloud Free Tier deployment plan
```

---

[Next: Architecture](02-architecture.md)
