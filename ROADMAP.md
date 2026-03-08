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
| ✅ Done | Comprehensive test suite | 199 tests across 7 modules covering database, app, config, models, moderator, sessions, and tools |
| ⬜ Planned | Refactor large modules | Target < 500 lines per file for maintainability |
| **Specialist Plugins** | | |
| ⬜ Planned | Plugin system for specialists | Domain-expert LLM plugins (e.g. a medical specialist that searches Medline to ground discussions in verifiable facts) |
| ⬜ Planned | Specialist consultation tool | Allow any participant to request opinion, verification, or input from a specialist during a discussion |
| **Training Data & Model Development** | | |
| ⬜ Planned | Open-source reasoning datasets | Harvest high-quality discussion outcomes as open datasets for reasoning AI research |
| ⬜ Planned | Small moderator models | Train lightweight moderator models from collected data, targeting local consumer hardware |
