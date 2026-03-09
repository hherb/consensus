# Roadmap

This document tracks planned and implemented features for Consensus, grouped by theme.

| Status | Feature | Details |
|--------|---------|---------|
| **Discussion Continuity** | | |
| ✅ Done | Resume previous discussions | `app.load_discussion()`, `app.resume_discussion()`, `app.reopen_discussion()`; exposed via desktop bridge and REST API |
| ✅ Done | Dynamic participation | Entities can be added or removed from an ongoing discussion mid-session |
| **Institutional Memory** | | |
| ✅ Done | Long-term memory for AI participants | Persistent memory across discussions so AIs can build on prior reasoning and positions |
| ✅ Done  | Semantic search over past discussions | Embedding-based retrieval of relevant passages from the corpus of past discussions |
| ✅ Done  | Knowledge graph | Extract and query concepts, positions, and relationships from discussion history |
| **Research-Grade Argumentation** | | |
| ✅ Done | Web search and tool access | `tools.py` (ToolProvider ABC, ToolRegistry), `tools_builtin.py` (Brave Search + DuckDuckGo fallback), native function calling in `ai_client.py`, tool execution loop in `moderator.py` |
| ✅ Done | Devil's Advocate role | Constructive-critic participant role with dedicated prompt templates (`system_devils_advocate`, `turn_devils_advocate`) that challenge assumptions and identify weaknesses |
| **Democratic Moderation** | | |
| ⬜ Planned | Participant voting system | Any participant (human or AI) can propose a poll during discussion. Vote types: early conclusion (consensus reached, no further turns needed), extend discussion (max rounds hit but issues unresolved), invoke expert consultation, change topic focus, or custom motions. Moderator presents the motion, collects votes with optional rationale, and announces the outcome. Configurable thresholds (simple majority, supermajority, unanimous). Results logged as structured messages for audit trail |
| ⬜ Planned | Moderation challenges | Entities formally challenge moderator summaries/decisions; reviewed by participant consensus |
| ⬜ Planned | Moderator elections | Participants vote to replace or change moderator during a discussion |
| **Authentication & Identity** | | |
| ✅ Done | Registration and authentication | `auth.py`: email/password (PBKDF2-SHA256, 600k iterations), OAuth (GitHub, Google, LinkedIn, Apple), httpOnly bearer tokens, brute-force rate limiting, multiple OAuth identities per user |
| **Public Service** | | |
| ✅ Done | Security hardening | `server.py`: rate limiting, security headers, CORS, CSRF, path traversal protection, auth middleware; `session.py`: per-session isolated app + SQLite with TTL expiry (`--multi-user`) |
| ⬜ Planned | Free hosted instance | Public deployment once hosting costs are resolved |
| **Evaluation & Benchmarking** | | |
| ✅ Done | Evaluation framework | Ablation study platform with 10 medical cases, 5 conditions, batch runner, string-match + LLM-judge scoring, per-participant provider/model overrides, web UI at `/eval/` |
| ✅ Done | Max rounds limit | Discussions auto-conclude after N rounds (configurable, 0 = unlimited) |
| **Reliability** | | |
| ✅ Done | Retry with exponential backoff | API calls retry up to 3× for transient errors (429, 5xx, timeouts); failed participants skipped gracefully |
| ✅ Done | OpenAI API compatibility | Uses `max_completion_tokens` for newer models, DeepSeek DSML tool-call parsing |
| **Code Quality & Maintainability** | | |
| ✅ Done | Database migration system | File-based SQL migrations in `consensus/migrations/`, tracked in `migrations` table, run idempotently on startup (`migrator.py`) |
| ✅ Done | Comprehensive test suite | 299 tests across 7+ modules covering database, app, config, models, moderator, sessions, tools, and documents |
| ✅ Done | Refactor large modules | `ConsensusApp` split into `app_providers.py`, `app_entities.py`, `app_discussion_setup.py`, `app_discussion_flow.py`, `app_discussion_state.py`; `Database` split into `db/` subpackage with 9 domain-specific mixins; `app.js` refactored into ES modules |
| **Specialist Plugins** | | |
| ✅ Done | MCP client (stdio transport) | MCPToolProvider class connecting to external MCP servers via stdio; expert entities that get one turn when invoked then step back |
| ✅ Done | Expert invocation UI | AI entities invoke experts via tool calls; humans trigger consultation via UI button; progress notifications shown as live typing indicator with stage text and progress count |
| ✅ Done | MCP server management UI | Register/configure MCP servers in the Providers tab, stored in database |
| ⬜ Planned | MCP Streamable HTTP transport | Connect to remote MCP servers over HTTP+SSE in addition to stdio |
| ⬜ Planned | Multiple simultaneous expert consultations | Invoke several experts in parallel during a single turn |
| ⬜ Planned | Expert-to-expert chaining | Allow one expert to invoke another expert as part of its work |
| ⬜ Planned | Config file-based MCP server definitions | JSON/TOML config for deployment-managed MCP server defaults |
| ⬜ Planned | MCP resources and prompts | Support MCP resources and prompt templates beyond tool calls |
| **Document RAG** | | |
| ✅ Done | Document ingestion & parsing | `tools_document.py`: URL/text/PDF/HTML ingestion with pdfplumber, trafilatura; auto-chunking with paragraph-aware boundaries and configurable overlap |
| ✅ Done | RAG-powered Q&A | Embed question, retrieve top-k chunks by cosine similarity, LLM-generated answer with passage citations |
| ✅ Done | Document navigation tools | `doc_get_sections`, `doc_get_chapter`, `doc_get_text`, `doc_get_length` for structured document browsing |
| ✅ Done | Cross-discussion document library | `doc_list` with full_library mode for semantic search across all documents; per-discussion document binding |
| ✅ Done | Map-reduce summarization | `doc_summary` handles arbitrarily long documents by summarizing chunks then synthesizing |
| **Architecture & Scalability** | | |
| ⬜ Planned | Participant-driven context loading | Replace centralized in-memory Discussion state with direct DB access per participant. Each participant queries only the context it needs (full history, sliding window, or map-reduce over long context). Eliminates state conflicts since writes are limited to the participant's own new contribution. Enables arbitrarily long discussions without memory pressure, and allows different participants to use different context strategies simultaneously |
| **Training Data & Model Development** | | |
| ⬜ Planned | Open-source reasoning datasets | Harvest high-quality discussion outcomes as open datasets for reasoning AI research |
| ⬜ Planned | Small moderator models | Train lightweight moderator models from collected data, targeting local consumer hardware |
