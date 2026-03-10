# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Consensus is a moderated discussion platform enabling structured multi-party dialogues between humans and AI entities. A designated moderator (human or AI) manages discussion flow, turn-taking, and synthesis. Supports both desktop (pywebview) and web (aiohttp) modes sharing the same backend.

## Commands

**This project uses `uv` for all Python package installation and environment management. Never use `pip` directly.**

```bash
# Install
uv pip install -e .              # base (httpx only)
uv pip install -e ".[desktop]"   # + pywebview
uv pip install -e ".[web]"       # + aiohttp
uv pip install -e ".[all]"       # everything

# Run
python -m consensus            # desktop mode (default)
python -m consensus --web      # web server mode (single-user)
python -m consensus --web --multi-user  # multi-user mode (public deployment)
python -m consensus --web --port 8080 --debug

# CLI entry point
consensus                      # via pyproject.toml [project.scripts]
```

Initial pytest test suite in `tests/`. No linter or build system configured yet.

## Architecture

**Dual-mode app:** Both desktop (pywebview + JS bridge) and web (aiohttp REST API) modes route through the same `ConsensusApp` orchestrator.

```
Frontend (static HTML/CSS/JS in consensus/static/)
    ↕ pywebview bridge OR aiohttp REST API
ConsensusApp (app.py) — orchestrator, state management, event emitter
    ├── app_providers.py — provider management
    ├── app_entities.py — entity CRUD
    ├── app_discussion_setup.py — discussion creation & configuration
    ├── app_discussion_flow.py — turn flow operations
    ├── app_discussion_state.py — discussion state management
    ├── Moderator (moderator.py) — turn flow, AI generation, summaries
    ├── AIClient (ai_client.py) — async OpenAI-compatible HTTP client
    ├── PricingCache (pricing.py) — model cost lookup via OpenRouter
    ├── MCPToolProvider (mcp_client.py) — MCP server communication (JSON-RPC 2.0)
    ├── DocumentRAG (tools_document.py) — document ingestion, chunking, RAG Q&A
    └── Database (db/) — thread-safe SQLite persistence (domain-specific mixins)
```

**Key modules:**
- `models.py` — dataclasses: `Entity`, `AIConfig`, `Message`, `Discussion`, `StoryboardEntry`
- `config.py` — platform-aware data dirs (macOS: `~/Library/Application Support/consensus`)
- `desktop.py` — `DesktopBridge` exposes async Python to JS via pywebview; runs background event loop
- `server.py` — aiohttp routes mapping to `ConsensusApp` methods; serves static files with path traversal protection; includes rate limiting, security headers, CORS, CSRF protection, auth middleware, health endpoint
- `session.py` — `SessionManager` for multi-user deployments; per-session `ConsensusApp` + SQLite with TTL-based expiry
- `auth.py` — `AuthManager`, `AuthDatabase`, `User` model, PBKDF2-SHA256 password hashing, OAuth Authorization Code flow (GitHub, Google, LinkedIn, Apple), bearer token management
- `pricing.py` — `PricingCache` for per-message cost calculation using OpenRouter pricing data; fuzzy model name matching with aliases and variant generation
- `mcp_client.py` — `MCPToolProvider` for JSON-RPC 2.0 communication with MCP server subprocesses; expert entity consultation
- `tools_document.py` — Document RAG tool provider: ingestion (URL/text/PDF/HTML), chunking, embedding, RAG Q&A, section navigation, map-reduce summarization
- `db/` — Database subpackage with domain-specific mixins: `providers.py`, `entities.py`, `discussions.py`, `messages.py`, `prompts.py`, `tools.py`, `mcp.py`, `memory.py`, `documents.py`

**Database schema (SQLite, 15+ tables + auth):** `providers`, `entities` (types: human/ai/expert), `prompts`, `discussions`, `discussion_members`, `messages` (includes `cost` column), `storyboard_entries`, `model_pricing`, `mcp_servers`, `expert_definitions`, `tool_providers`, `entity_tools`, `discussion_tool_overrides`, `documents`, `document_chunks`, `document_chunk_embeddings`, `discussion_documents`, `images`, `discussion_images`, `message_images`. Auth tables (in separate `auth.db` for multi-user): `users`, `auth_tokens`, `user_oauth_identities`, `oauth_states`. Seeded with default moderator/participant prompt templates on first run.

**Migrations:** Numbered SQL files in `consensus/migrations/` (e.g. `007_images.sql`) are auto-discovered and applied by `migrator.py`. The migrator uses regex `^(\d{3})_.*\.sql$` to find files, tracks applied versions in a `migrations` table, and applies each migration exactly once in version order. New migrations only need to be added as files — no registration required.

**Frontend:** Vanilla JS in `consensus/static/` organized as ES modules. Tabbed setup UI (New Discussion, Providers, Profiles, Prompts, History) and live discussion view. Uses CSS custom properties for light/dark mode.

## Key Design Decisions

- All AI calls and HTTP operations are async (`httpx.AsyncClient`). Desktop mode bridges sync/async via a background event loop thread.
- SQLite writes protected by `threading.Lock` for concurrent access from pywebview JS threads.
- `AIClient` targets any OpenAI-compatible API endpoint — provider registry allows multiple backends.
- Prompt templates stored in database, customizable per role (moderator vs participant) and task (turn, summary, conclusion, mediation).
- `Discussion` object held in memory as current session state; historical data persisted to SQLite.
- BYOK (Bring Your Own Key): In web mode, users can provide API keys via the browser UI (stored in `sessionStorage`). Keys are sent per-request and never persisted on the server. Environment-based keys remain the default fallback.
- Multi-user mode (`--multi-user`): Each browser session gets its own `ConsensusApp` instance and SQLite database, isolated by session cookie. Sessions expire after 24h of inactivity.
- Authentication (multi-user only): Email/password registration with PBKDF2-SHA256 hashing (600k iterations). OAuth via GitHub, Google, LinkedIn, Apple. Auth tokens are SHA-256 hashed in storage, set as httpOnly cookies (never returned in response body). CSRF protection via Content-Type enforcement. Per-email brute-force rate limiting (5 attempts/5min). OAuth redirect URIs derived from `CONSENSUS_BASE_URL` env var (not request headers). Multiple OAuth identities per user supported via `user_oauth_identities` table.

## General coding rules that must be observed
docs/llm/golden_rules.md

## License

GNU AGPL-3.0-or-later
