# Consensus -- Programmer's Manual

*For contributors new to the codebase*

---

This manual is split into the following sections. Each links to a dedicated
document for detailed coverage.

## Contents

| # | Document | Topics |
|---|----------|--------|
| 1 | [Getting Started](01-getting-started.md) | What is Consensus, dev environment setup, repository layout |
| 2 | [Architecture](02-architecture.md) | High-level architecture, module overview (models, config, database, ai_client, moderator) |
| 3 | [Backend Modules](03-backend-modules.md) | Detailed guide: app.py, server.py, session.py, desktop.py, \_\_main\_\_.py |
| 4 | [Frontend](04-frontend.md) | index.html structure, style.css theming, app.js application logic |
| 5 | [Database](05-database.md) | Full schema reference, seeding, migrations |
| 6 | [Data Flow and Lifecycle](06-data-flow-and-lifecycle.md) | End-to-end turn flow, discussion lifecycle (setup, active, paused, concluded) |
| 7 | [API Reference](07-api-reference.md) | REST API (web mode), pywebview bridge (desktop mode) |
| 8 | [Tool Use](08-tool-use.md) | Pluggable tool framework, tool registry, built-in web search, access control |
| 9 | [Prompts, Providers, and Security](09-prompts-providers-security.md) | Prompt template system, AI provider integration, BYOK, security measures |
| 10 | [Contributing](10-contributing.md) | Common tasks, conventions, patterns, debugging, known limitations |
| 11 | [Authentication](11-authentication.md) | Email/password auth, OAuth (GitHub/Google/LinkedIn/Apple), tokens, CSRF, brute-force protection |

## Quick Architecture Diagram

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

## Key Design Principles

- **Dual-mode, single backend.** Desktop (pywebview) and web (aiohttp) modes
  funnel all logic through `ConsensusApp`.
- **Async by default.** All AI and HTTP operations are async. Desktop mode
  bridges sync/async via a background event loop thread.
- **Provider-agnostic AI.** Any OpenAI-compatible API endpoint works. The
  provider registry supports multiple backends.
- **Pluggable tool use.** AI entities can call tools (web search, etc.) during
  turn generation via an iterative tool execution loop.
- **BYOK (Bring Your Own Key).** In multi-user web mode, users provide API keys
  per-request via the browser. Keys are never persisted server-side.
- **Soft-delete for referential integrity.** Entities referenced in past
  discussions are soft-deleted (marked inactive) rather than hard-deleted.

---

*This manual reflects the codebase as of March 2026. If you find it out of
date, please update it as part of your contribution.*
