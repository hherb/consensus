# Roadmap

This document tracks planned and implemented features for Consensus, grouped by theme.

| Status | Feature | Details |
|--------|---------|---------|
| **Discussion Continuity** | | |
| ✅ Done | Resume previous discussions | `app.load_discussion()`, `app.resume_discussion()`, `app.reopen_discussion()`; exposed via desktop bridge and REST API |
| ✅ Done | Dynamic participation | Entities can be added or removed from an ongoing discussion mid-session |
| **Institutional Memory** | | |
| ⬜ Planned | Long-term memory for AI participants | Persistent memory across discussions so AIs can build on prior reasoning and positions |
| ⬜ Planned | Semantic search over past discussions | Embedding-based retrieval of relevant passages from the corpus of past discussions |
| ⬜ Planned | Knowledge graph | Extract and query concepts, positions, and relationships from discussion history |
| **Research-Grade Argumentation** | | |
| ✅ Done | Web search and tool access | `tools.py` (ToolProvider ABC, ToolRegistry), `tools_builtin.py` (Brave Search + DuckDuckGo fallback), native function calling in `ai_client.py`, tool execution loop in `moderator.py` |
| **Democratic Moderation** | | |
| ⬜ Planned | Moderation challenges | Entities formally challenge moderator summaries/decisions; reviewed by participant consensus |
| ⬜ Planned | Moderator elections | Participants vote to replace or change moderator during a discussion |
| **Authentication & Identity** | | |
| ✅ Done | Registration and authentication | `auth.py`: email/password (PBKDF2-SHA256, 600k iterations), OAuth (GitHub, Google, LinkedIn, Apple), httpOnly bearer tokens, brute-force rate limiting, multiple OAuth identities per user |
| **Public Service** | | |
| ✅ Done | Security hardening | `server.py`: rate limiting, security headers, CORS, CSRF, path traversal protection, auth middleware; `session.py`: per-session isolated app + SQLite with TTL expiry (`--multi-user`) |
| ⬜ Planned | Free hosted instance | Public deployment once hosting costs are resolved |
| **Training Data & Model Development** | | |
| ⬜ Planned | Open-source reasoning datasets | Harvest high-quality discussion outcomes as open datasets for reasoning AI research |
| ⬜ Planned | Small moderator models | Train lightweight moderator models from collected data, targeting local consumer hardware |
