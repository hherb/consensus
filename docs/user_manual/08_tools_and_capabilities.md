# Chapter 8: Tools and Capabilities

AI participants can use tools during their turns — capabilities that go beyond pure text generation. Tools are assigned per-entity in the Profiles tab and can be overridden per-discussion.

## Web Search

Enables AI participants to search the internet and fetch web pages.

**Tools provided:**
- `web_search(query, num_results)` — Searches using Brave Search API (primary) or DuckDuckGo (fallback). Returns titles, URLs, and snippets for up to 10 results.
- `fetch_webpage(url, max_chars)` — Fetches a URL and extracts readable text content (up to 8000 characters by default).

**Setup:** For Brave Search, set the `BRAVE_SEARCH_API_KEY` environment variable. DuckDuckGo works without any key but has lower quality results.

**When to enable:** When participants need current information, need to fact-check claims, or the topic requires real-world data not in the model's training data.

## Ask User (Interactive Input)

Allows AI participants to pause mid-turn and ask the human user a question.

**Tools provided:**
- `ask_user(question, context)` — Pauses generation, shows an inline input bubble in the chat, waits for user response (5-minute timeout).

**When to enable:** When you want AI participants to be able to clarify ambiguities, request additional information, or check assumptions with the user during their turn.

## Document Tools

Provides AI participants with access to documents attached to the discussion and across the document library. See [Chapter 9](09_documents_and_images.md) for document management details.

**Tools provided:**
- `doc_add(url/text/filename, title)` — Add a new document
- `doc_list(full_library, query)` — List discussion documents or search the full library
- `doc_get_length(document_id)` — Get document size
- `doc_get_text(document_id, from_char, to_char)` — Read a specific range
- `doc_get_sections(document_id)` — List markdown headers with offsets
- `doc_get_chapter(document_id, header)` — Read a specific section
- `doc_ask(document_id, question)` — RAG-powered Q&A with citations
- `doc_summary(document_id, from_char, to_char)` — Map-reduce summarisation

**When to enable:** When the discussion involves specific documents, papers, reports, or data that participants should be able to reference.

## Sandboxed Python Execution

Allows AI participants to write and execute Python code in a secure sandbox.

**Tools provided:**
- `execute_python(code, description)` — Run Python code in a subprocess with restricted access. REPL-like: the value of the last expression is captured and returned.
- `install_python_package(package_name, reason)` — Request installation of a PyPI package (triggers a user approval dialog).

**Available libraries:** Standard library modules (math, statistics, collections, itertools, json, re, datetime, csv, etc.) plus scientific and ML libraries when installed (numpy, scipy, pandas, matplotlib, torch, etc.).

**Security:** Code runs in a subprocess with:
- No network access
- No system commands
- File I/O limited to a temporary directory
- Resource limits: 70% of free RAM, 70% of CPU cores
- Optional macOS sandbox-exec enforcement

**When to enable:** When participants need to perform calculations, data analysis, statistical tests, or generate structured output programmatically.

## Memory Tools

Gives AI participants persistent memory across discussions. See [Chapter 10](10_institutional_memory.md) for the full memory system.

**Tools provided:**
- `memory_store(content)` — Save a persistent memory
- `memory_recall(query, limit)` — Semantic search over personal memories
- `memory_forget(memory_id)` — Delete a specific memory
- `discussion_search(query, limit, topic_filter)` — Search across all past discussion messages
- `kg_assert(subject, relation, object, evidence)` — Add a knowledge graph triple
- `kg_query(query, mode, limit)` — Search or traverse the knowledge graph

**Requires:** An embedding service (see [Chapter 10](10_institutional_memory.md) for configuration).

**When to enable:** For long-running research programs where AI participants should build on their prior reasoning across multiple discussions.

## Image Tools

Enables AI participants to work with images attached to the discussion.

**Tools provided:**
- `describe_image(image_id)` — Get an LLM-generated text description of an image
- `list_images()` — List all images in the current discussion
- `add_image_url(url, title)` — Add an image from a URL

**Note:** Vision-capable models (GPT-4o, Claude 3+, Gemini) receive images directly as visual content in their context — they don't need the describe tool. The describe tool exists for non-vision models that can only process text.

## Expert Consultation

AI participants can consult expert entities backed by MCP servers. This is handled through the standard tool-calling mechanism — the AI invokes the expert and receives a response.

**Tools provided:**
- `consult_expert(expert_name, question)` — Put a question to a configured expert entity and receive its answer. The `expert_name` argument is populated at runtime with the experts available in the discussion
- `list_available_experts()` — Discover configured expert entities. Paired automatically whenever `consult_expert` is enabled

See [Chapter 15](15_mcp_integration.md) for configuring expert entities and MCP servers.

## How Tools Appear in the Chat

When an AI uses a tool, the chat shows:
- A collapsible `<details>` element with the tool name
- Click to expand and see: arguments passed, result returned, execution time, success/error status
- A typing indicator with stage text (e.g., "Calling web_search...") while the tool executes

AI participants may use multiple tools in sequence during a single turn (up to 5 iterations).

## Tool Access Modes

Tools can be configured with different access modes:
- **Private** — Only the assigned entity can use this tool
- **Shared** — All participants in the discussion can use it
- **Moderator Only** — Only the moderator can use it
