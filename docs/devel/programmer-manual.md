# Consensus -- Programmer's Manual

*For contributors new to the codebase*

---

This manual is split into the following sections. Each links to a dedicated
document for detailed coverage.

## Contents

| # | Document | Topics |
|---|----------|--------|
| 1 | [Getting Started](01-getting-started.md) | What is Consensus, dev environment setup, repository layout |
| 2 | [Architecture](02-architecture.md) | High-level architecture, module overview (models, config, db/, ai_client, moderator) |
| 3 | [Backend Modules](03-backend-modules.md) | Detailed guide: app.py + app_*.py modules, server.py, session.py, desktop.py |
| 4 | [Frontend](04-frontend.md) | ES module structure, index.html, style.css theming, API adapters |
| 5 | [Database](05-database.md) | Full schema reference (including document and image tables), seeding, migrations |
| 6 | [Data Flow and Lifecycle](06-data-flow-and-lifecycle.md) | End-to-end turn flow, discussion lifecycle (setup, active, paused, concluded) |
| 7 | [API Reference](07-api-reference.md) | REST API (web mode), document/image endpoints, pywebview bridge (desktop mode) |
| 8 | [Tool Use](08-tool-use.md) | Pluggable tool framework, web search, document RAG, image tools, memory, access control |
| 9 | [Prompts, Providers, and Security](09-prompts-providers-security.md) | Prompt template system, AI provider integration, BYOK, security measures |
| 10 | [Contributing](10-contributing.md) | Common tasks, conventions, patterns, debugging, known limitations |
| 11 | [Authentication](11-authentication.md) | Email/password auth, OAuth (GitHub/Google/LinkedIn/Apple), tokens, CSRF, brute-force protection |
| 12 | [Cost Tracking](12-cost-tracking.md) | PricingCache, OpenRouter integration, per-message cost, model name matching |
| 13 | [MCP Expert Plugins](13-mcp-expert-plugins.md) | MCPToolProvider, expert entities, consult_expert meta-tool, SSE progress events |

## Quick Architecture Diagram

```
Frontend (static/ — vanilla JS ES modules)
    |
    | pywebview JS bridge       OR       aiohttp REST API
    | (desktop.py:DesktopBridge)          (server.py)
    |
    v
ConsensusApp (app.py + app_*.py domain modules)
    |  Central orchestrator: state management, validation, DB writes, event emitter
    |
    +-- Moderator (moderator.py)
    |     Turn flow, prompt resolution, AI generation, tool execution loop,
    |     multimodal context (images for vision-capable models)
    |
    +-- ToolRegistry (tools.py)
    |     Pluggable tool providers, access control, execution with timeout
    |
    +-- MCPToolProvider (mcp_client.py)
    |     JSON-RPC 2.0 over stdio (local MCP server subprocesses)
    |
    +-- MCPHTTPToolProvider (mcp_http_client.py)
    |     JSON-RPC 2.0 over Streamable HTTP+SSE (remote MCP servers)
    |
    +-- DocumentRAG (tools_document.py)
    |     Document ingestion, chunking, RAG Q&A, summarization
    |
    +-- ImageTools (tools_image.py)
    |     Image storage, vision-model description, multimodal context
    |
    +-- PricingCache (pricing.py)
    |     Model cost lookup via OpenRouter, fuzzy name matching
    |
    +-- AIClient (ai_client.py)
    |     Async HTTP via httpx to any OpenAI-compatible endpoint
    |
    +-- Database (db/)
          Thread-safe SQLite subpackage with domain-specific mixins:
          providers, entities, prompts, discussions, messages, tools,
          MCP/experts, memory, documents, images
```

## Key Design Principles

- **Dual-mode, single backend.** Desktop (pywebview) and web (aiohttp) modes
  funnel all logic through `ConsensusApp`.
- **Async by default.** All AI and HTTP operations are async. Desktop mode
  bridges sync/async via a background event loop thread.
- **Provider-agnostic AI.** Any OpenAI-compatible API endpoint works. The
  provider registry supports multiple backends.
- **Pluggable tool use.** AI entities can call tools (web search, document
  analysis, image description, etc.) during turn generation via an iterative
  tool execution loop.
- **Document RAG.** Documents (URL, PDF, HTML, text) can be ingested, chunked,
  and queried via RAG tools during discussions.
- **Image support.** Images can be attached to discussions for visual context.
  Vision-capable models receive them as multimodal content blocks; non-vision
  models can use the `describe_image` tool.
- **BYOK (Bring Your Own Key).** In multi-user web mode, users provide API keys
  per-request via the browser. Keys are never persisted server-side.
- **Soft-delete for referential integrity.** Entities referenced in past
  discussions are soft-deleted (marked inactive) rather than hard-deleted.
- **Institutional memory (optional).** AI entities can persist observations,
  search past discussions semantically, and maintain a knowledge graph across
  sessions. Implemented as tool providers requiring `[memory]` extras + Ollama.
- **MCP expert plugins.** External tools exposed via MCP servers can be wrapped
  as consultable expert entities, extending capabilities without modifying core
  code.
- **Cost tracking.** Per-message cost calculation using OpenRouter pricing data,
  with fuzzy model name matching and automatic cache refresh.

---

*This manual reflects the codebase as of March 2026. If you find it out of
date, please update it as part of your contribution.*
