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
| **Discussion Methods** | | |
| ✅ Done | Pluggable discussion method system | `consensus/methods/` — `DiscussionMethod` ABC with phase management, prompt hooks, response post-processing, round lifecycle hooks, turn ordering, and serialisation. Method registry in `__init__.py`. Integrated into moderator and discussion flow |
| ✅ Done | Open Discussion method | Default round-robin with optional Devil's Advocate — wraps existing behaviour as a method |
| ✅ Done | Analysis of Competing Hypotheses (ACH) | Intelligence-analysis method: enumerate hypotheses → gather evidence → rate each hypothesis against each evidence piece → identify diagnostic evidence. Four phases with hypothesis matrix tracking |
| ✅ Done | Belief State Diffusion | LLM-native method: participants maintain explicit probability distributions over hypotheses, update beliefs each round with justification, automatic convergence detection. Produces graphable belief trajectories |
| ✅ Done | Delphi Method | Independent anonymous responses across multiple rounds; facilitator shares statistical distribution + anonymised reasoning; participants revise. Avoids anchoring, authority bias, and social pressure. `consensus/methods/delphi.py` — estimate → revise (condition-based convergence) → synthesise |
| ✅ Done | Premortem Analysis | Assume a preliminary conclusion is reached, then each participant independently constructs a narrative of how and why it failed. Psychologically easier than critiquing a live idea. `consensus/methods/premortem.py` — frame → premortem → consolidate |
| ✅ Done | Key Assumptions Check | Explicitly surface and challenge the assumptions underlying the question before analysis begins. Can function standalone or as a mandatory first phase in other methods. `consensus/methods/key_assumptions.py` — surface → challenge → assess |
| ✅ Done | Adversarial Collaboration (Kahneman-style) | Participants who genuinely disagree jointly design the criteria that would settle the question *before* gathering evidence. Prevents post-hoc rationalisation. `consensus/methods/adversarial_collab.py` — positions → criteria → evidence → adjudicate |
| ✅ Done | Red Team / Blue Team with Rotation | Rotating adversarial role each round; red team sees only the current conclusion and tries to break it. Red team does not participate in construction, only destruction. `consensus/methods/red_team.py` — construct → attack → revise → assess |
| ⬜ Planned | Tournament / Superforecasting | Independent probabilistic estimates, weighted by track record (à la Tetlock). Answer is a calibrated probability distribution, not an agreed narrative. Best for empirical questions. **Difficulty: Medium** — needs numeric extraction (like Delphi) plus cross-discussion track-record weighting |
| ⬜ Planned | Argument Mapping | Build a directed graph of claims, reasons, objections, and rebuttals (Kialo/Argdown style). Makes logical structure explicit; moderator maintains argument structure rather than managing turns. **Difficulty: Medium** — needs graph data structure in method_state and ideally frontend visualisation |
| ⬜ Planned | Counterfactual Stress Testing | For each key claim in a developing consensus, systematically invert it and check if the conclusion survives. Produces a dependency graph of load-bearing vs. decorative beliefs. **Difficulty: Medium** — needs claim extraction and dependency tracking |
| ⬜ Planned | Recursive Self-Distillation | LLM-native: generate rich reasoning → strip to pure logical skeleton (premises/inferences/conclusion) → blind-evaluate only the skeleton. Separates persuasiveness from validity. **Difficulty: Medium** — needs response transformation between phases |
| ⬜ Planned | Adversarial Decomposition | Decompose the question into logical atoms (smallest independently evaluable sub-claims), assign participants to attack different atoms, then reconstruct which conclusions survive. Surgical rather than holistic evaluation. **Difficulty: Medium** — needs claim extraction, assignment tracking, and survival scoring |
| ⬜ Planned | Epistemic Bootstrapping | LLM-native: start with minimal-context agent, feed evidence one piece at a time, track which pieces actually change the conclusion vs. add rhetorical weight. Measures information value by exploiting LLMs' freedom from the curse of knowledge. **Difficulty: Hard** — requires per-participant context isolation (not currently supported) |
| ⬜ Planned | Multi-Scale Concurrent Reasoning | LLM-native: three participants operate at different abstraction levels simultaneously (data/facts, mechanisms/theories, meta-patterns/analogies). Cross-pollinate each round. Forces persistent multi-scale analysis. **Difficulty: Hard** — requires parallel generation within a single turn |
| ⬜ Planned | Temperature Gradient Exploration | LLM-native: run the same prompt at temperatures 0.0–1.5 in parallel, then a cold evaluator mines genuinely novel framings from high-temperature outputs. Systematic creativity mining. **Difficulty: Hard** — requires parallel AI calls with different parameters for the same entity |
| ⬜ Planned | Parallel Diverge-Converge Cycles | Multiple independent exploration threads that periodically merge. A synthesis agent identifies agreements, contradictions, and gaps to seed the next divergence round. Santa Fe Institute style. **Difficulty: Hard** — requires concurrent discussion threads (fundamentally different from single-thread model) |
| **Interactive User Input** | | |
| ✅ Done | Ask-user tool | `tools_ask_user.py`: AI participants can pause mid-turn to request input from the human user. Uses `asyncio.Future` to block the tool execution loop while the frontend displays an inline input bubble. 5-minute timeout, reconnection-safe via `pending_user_input` in app state. SSE and pywebview event support |
| **Research-Grade Argumentation** | | |
| ✅ Done | Web search and tool access | `tools.py` (ToolProvider ABC, ToolRegistry), `tools_builtin.py` (Brave Search + DuckDuckGo fallback), native function calling in `ai_client.py`, tool execution loop in `moderator.py` |
| ✅ Done | Devil's Advocate role | Constructive-critic participant role with dedicated prompt templates (`system_devils_advocate`, `turn_devils_advocate`) that challenge assumptions and identify weaknesses |
| **Democratic Moderation** | | |
| ✅ Done | Participant voting system | `consensus/methods/voting.py` — three-phase discussion method (deliberate → vote → tally). Participants propose motions via JSON blocks during deliberation, then vote for/against/abstain on each motion. Configurable thresholds (simple majority, supermajority, unanimous). Double-vote prevention, vote validation, tally with pass/fail determination. Results synthesised by moderator |
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
| ✅ Done | Comprehensive test suite | 425 tests across 20+ modules covering database, app, config, models, moderator, sessions, tools, documents, MCP client/server, pricing, methods |
| ✅ Done | Refactor large modules | `ConsensusApp` split into `app_providers.py`, `app_entities.py`, `app_discussion_setup.py`, `app_discussion_flow.py`, `app_discussion_state.py`; `Database` split into `db/` subpackage with 9 domain-specific mixins; `app.js` refactored into ES modules |
| **Specialist Plugins** | | |
| ✅ Done | MCP client (stdio transport) | MCPToolProvider class connecting to external MCP servers via stdio; expert entities that get one turn when invoked then step back |
| ✅ Done | Expert invocation UI | AI entities invoke experts via tool calls; humans trigger consultation via UI button; progress notifications shown as live typing indicator with stage text and progress count |
| ✅ Done | MCP server management UI | Register/configure MCP servers in the Providers tab, stored in database |
| ✅ Done | MCP Streamable HTTP transport | `mcp_http_client.py`: `MCPHTTPToolProvider` connects to remote MCP servers over HTTP+SSE. Session management via `Mcp-Session-Id`, SSE response parsing, retry with exponential backoff. Migration 008 adds `transport`/`url`/`headers` columns. Frontend transport selector toggles stdio vs HTTP fields |
| ⬜ Planned | Multiple simultaneous expert consultations | Invoke several experts in parallel during a single turn |
| ⬜ Planned | Expert-to-expert chaining | Allow one expert to invoke another expert as part of its work |
| ✅ Done | Config file-based MCP server definitions | `mcp_config.py`: Load MCP servers from JSON/TOML config files at startup. Searches `CONSENSUS_MCP_CONFIG` env var, `./mcp_servers.json`, `~/.consensus/`, and platform data dir. New servers added to DB; changed servers updated. Supports both stdio and HTTP transport entries |
| ✅ Done | MCP server for external AI agents | `mcp_server.py`: Consensus as an MCP server (stdio JSON-RPC 2.0). 13 tools: list/read/search discussions, entities, documents, memories, knowledge graph; store/delete memories, assert knowledge triples, run full automated discussions. Entry point `consensus-mcp`. Agent gets persistent memory via auto-created "Claude Code Agent" entity. Memory deletion ownership-enforced |
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
