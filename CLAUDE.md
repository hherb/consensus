# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Consensus is a moderated discussion platform enabling structured multi-party dialogues between humans and AI entities. A designated moderator (human or AI) manages discussion flow, turn-taking, and synthesis. Supports both desktop (pywebview) and web (aiohttp) modes sharing the same backend.

## Commands

```bash
# Install
pip install -e .              # base (httpx only)
pip install -e ".[desktop]"   # + pywebview
pip install -e ".[web]"       # + aiohttp
pip install -e ".[all]"       # everything

# Run
python -m consensus            # desktop mode (default)
python -m consensus --web      # web server mode (single-user)
python -m consensus --web --multi-user  # multi-user mode (public deployment)
python -m consensus --web --port 8080 --debug

# CLI entry point
consensus                      # via pyproject.toml [project.scripts]
```

No test suite, linter, or build system is configured yet.

## Architecture

**Dual-mode app:** Both desktop (pywebview + JS bridge) and web (aiohttp REST API) modes route through the same `ConsensusApp` orchestrator.

```
Frontend (static HTML/CSS/JS in consensus/static/)
    ↕ pywebview bridge OR aiohttp REST API
ConsensusApp (app.py) — orchestrator, state management, callbacks
    ├── Moderator (moderator.py) — turn flow, AI generation, summaries
    ├── AIClient (ai_client.py) — async OpenAI-compatible HTTP client
    └── Database (database.py) — thread-safe SQLite persistence
```

**Key modules:**
- `models.py` — dataclasses: `Entity`, `AIConfig`, `Message`, `Discussion`, `StoryboardEntry`
- `config.py` — platform-aware data dirs (macOS: `~/Library/Application Support/consensus`)
- `desktop.py` — `DesktopBridge` exposes async Python to JS via pywebview; runs background event loop
- `server.py` — aiohttp routes mapping to `ConsensusApp` methods; serves static files with path traversal protection; includes rate limiting, security headers, CORS, health endpoint
- `session.py` — `SessionManager` for multi-user deployments; per-session `ConsensusApp` + SQLite with TTL-based expiry

**Database schema (SQLite, 7 tables):** `providers`, `entities`, `prompts`, `discussions`, `discussion_members`, `messages`, `storyboard_entries`. Seeded with default moderator/participant prompt templates on first run.

**Frontend:** Vanilla JS in `consensus/static/app.js`. Tabbed setup UI (New Discussion, Providers, Profiles, Prompts, History) and live discussion view. Uses CSS custom properties for light/dark mode.

## Key Design Decisions

- All AI calls and HTTP operations are async (`httpx.AsyncClient`). Desktop mode bridges sync/async via a background event loop thread.
- SQLite writes protected by `threading.Lock` for concurrent access from pywebview JS threads.
- `AIClient` targets any OpenAI-compatible API endpoint — provider registry allows multiple backends.
- Prompt templates stored in database, customizable per role (moderator vs participant) and task (turn, summary, conclusion, mediation).
- `Discussion` object held in memory as current session state; historical data persisted to SQLite.
- BYOK (Bring Your Own Key): In web mode, users can provide API keys via the browser UI (stored in `sessionStorage`). Keys are sent per-request and never persisted on the server. Environment-based keys remain the default fallback.
- Multi-user mode (`--multi-user`): Each browser session gets its own `ConsensusApp` instance and SQLite database, isolated by session cookie. Sessions expire after 24h of inactivity.

## License

GNU AGPL-3.0-or-later
