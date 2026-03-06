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

# Editable install with all optional dependencies (recommended)
uv pip install -e ".[all]"

# Or pick just the mode you need:
uv pip install -e ".[desktop]"   # pywebview only
uv pip install -e ".[web]"       # aiohttp only
uv pip install -e "."            # base (httpx only, no UI server)
```

The base install pulls in only `httpx` and `python-dotenv`. The `desktop`
extra adds `pywebview>=5.0`; the `web` extra adds `aiohttp>=3.9`; `all`
includes both.

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

### No test suite yet

There is currently no test suite, linter configuration, or CI pipeline. This
is an area ripe for contribution.

---

## Repository Layout

```
consensus/
  __init__.py          Package marker; defines __version__
  __main__.py          CLI entry point (argparse, mode selection)
  models.py            Dataclasses: Entity, AIConfig, Message, Discussion, ...
  config.py            Platform paths, .env loading, API key helpers
  database.py          SQLite persistence layer (10 tables)
  ai_client.py         Async OpenAI-compatible HTTP client (httpx)
  moderator.py         Turn flow, AI generation, prompt resolution, tool execution
  app.py               ConsensusApp orchestrator (all business logic)
  server.py            aiohttp web server (REST routes, middleware, static files)
  session.py           Multi-user session manager (per-session app + SQLite)
  desktop.py           pywebview launcher and JS-Python bridge
  tools.py             Pluggable tool framework (ToolProvider, ToolRegistry)
  tools_builtin.py     Built-in web search tool (Brave + DuckDuckGo fallback)
  static/
    index.html         Single-page HTML (setup + discussion views)
    style.css          All styling (dark/light themes via CSS custom properties)
    app.js             Entire frontend logic (vanilla JS SPA)
docs/
  plans/               Design documents for specific features
  devel/               Developer documentation (you are here)
pyproject.toml         Build config, dependencies, entry points
CLAUDE.md              Instructions for AI coding assistants
README.md              User-facing project overview
QUICKSTART.md          Quick start guide for end users
DEPLOYMENT.md          Oracle Cloud Free Tier deployment plan
```

---

[Next: Architecture](02-architecture.md)
