# Built-in Tools Reference

Consensus provides a set of built-in tools that AI participants can use during discussions. Tools are assigned to entities via the **Profiles** tab and are invoked autonomously by the LLM during turn generation via OpenAI-compatible function calling.

This document lists every built-in tool, its parameters, behavior, and configuration requirements. For the underlying architecture (ToolRegistry, access control, execution flow), see [docs/devel/08-tool-use.md](devel/08-tool-use.md).

---

## Table of Contents

- [Web Search](#web-search)
- [Fetch Webpage](#fetch-webpage)
- [Consult Expert](#consult-expert)
- [Ask User](#ask-user)
- [Execute Python](#execute-python)
- [Install Python Package](#install-python-package)
- [Document Tools](#document-tools)
- [Image Tools](#image-tools)
- [Memory Tools](#memory-tools)
- [Tool Assignment](#tool-assignment)

---

## Web Search

**Provider:** `builtin` · **Module:** `tools_builtin.py` · **No optional dependencies**

Search the web for current information using Brave Search API (primary) or DuckDuckGo (fallback).

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `query` | string | yes | — | The search query |
| `num_results` | integer | no | 5 | Number of results to return (1–10) |

**Configuration:**
- Set `BRAVE_SEARCH_API_KEY` environment variable for Brave Search (higher quality)
- Without the key, falls back to DuckDuckGo HTML scraping (no key required)

**Output:** Numbered list of results with title, URL, and snippet.

---

## Fetch Webpage

**Provider:** `builtin` · **Module:** `tools_builtin.py` · **Optional:** `trafilatura`

Fetch a URL and extract its readable text content. Use after `web_search` to read the full content of a found page.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `url` | string | yes | — | The URL of the web page to fetch |
| `max_chars` | integer | no | 8000 | Maximum characters to return |

**Output:** Extracted readable text from the page, truncated to `max_chars`.

---

## Consult Expert

**Provider:** `experts` · **Module:** `app.py` · **Requires:** configured MCP server + expert entity

Consult a specialist expert entity for authoritative analysis. Expert entities wrap MCP tool servers as consultable participants.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `expert_name` | string | yes | — | Name of the expert entity to consult |
| `query` | string | yes | — | The question or claim to present to the expert |

**Output:** The expert's analysis response, added to the discussion context.

See [MCP Expert Plugins](devel/13-mcp-expert-plugins.md) for setup details.

---

## Ask User

**Provider:** `interactive` · **Module:** `tools_ask_user.py` · **No optional dependencies**

Pause the AI's turn to request input from the human user. The frontend displays an inline input bubble. The AI's tool loop blocks (via `asyncio.Future`) until the user responds or the 5-minute timeout expires.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `question` | string | yes | — | The question or request to present to the human user |
| `context` | string | no | — | Additional context explaining why the input is needed |

**Behavior:**
- An inline input bubble appears in the message flow
- The user types a response and clicks Submit
- The AI continues its turn incorporating the user's answer
- Times out after 5 minutes with an informative error
- Reconnection-safe: pending requests survive page reloads

---

## Execute Python

**Provider:** `python_exec` · **Module:** `tools_python.py` + `sandbox_worker.py` · **No optional dependencies**

Execute Python code in a secure, sandboxed subprocess. Designed for calculations, data analysis, string processing, ML experiments, and any computation that benefits from running actual code.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `code` | string | yes | — | Python code to execute |
| `description` | string | no | — | Brief description of what the code does (included in output) |

### Output format

The result includes up to three sections:

1. **Output** — anything written to stdout via `print()`
2. **Result** — the `repr()` of the last expression value (REPL-like behavior, similar to a Jupyter cell)
3. **Error** — full traceback if the code raises an exception

### Security sandbox

Code is executed in an isolated subprocess with multiple defense layers:

| Layer | What it does |
|-------|-------------|
| **AST pre-analysis** | Rejects blocked imports (`os`, `subprocess`, `socket`, etc.), dangerous calls (`exec()`, `eval()`, `compile()`), and dunder access (`__subclasses__`, `__globals__`, etc.) *before* any execution |
| **Subprocess isolation** | Code runs in a separate process — crashes, OOM kills, or infinite loops cannot affect the main application |
| **Restricted builtins** | Dangerous functions removed: `exec`, `eval`, `compile`, `__import__`, `breakpoint`, `getattr`, `setattr`, `delattr`, `memoryview`, `exit`, `quit`, `input`, `help`, `globals`, `locals`, `vars` |
| **Whitelisted imports** | Only modules in `ALLOWED_MODULES` can be imported; all others raise `ImportError` |
| **Sandboxed file I/O** | `open()` only allows paths inside a per-execution temp directory; `io.open` and `io.FileIO` are patched to enforce the same restriction |
| **Memory limit** | `RLIMIT_AS` set to 70% of available (free) system RAM (minimum 256 MB) |
| **CPU time limit** | `RLIMIT_CPU` set to `num_cores × 30s × 0.70` (minimum 10s) |
| **Wall-clock timeout** | Process killed after `CPU_limit × 1.5` seconds (minimum 30s) |
| **macOS sandbox-exec** | On macOS, wraps the subprocess with a Seatbelt profile that denies network access and restricts filesystem writes to the sandbox directory |

### Allowed modules

All of the following are available for `import`. Standard library modules are always present; scientific/ML libraries require prior installation (see [Install Python Package](#install-python-package)).

**Standard library:**

| Category | Modules |
|----------|---------|
| Math & numerics | `math`, `cmath`, `statistics`, `decimal`, `fractions`, `numbers`, `random` |
| Data structures | `collections`, `itertools`, `functools`, `operator`, `copy` |
| Serialization | `json`, `csv`, `struct` |
| Text processing | `re`, `string`, `textwrap`, `difflib`, `unicodedata` |
| Date & time | `datetime`, `time`, `calendar` |
| Hashing & encoding | `hashlib`, `base64`, `uuid`, `zlib` |
| Containers & algorithms | `bisect`, `heapq`, `array` |
| Formatting & types | `pprint`, `dataclasses`, `typing`, `enum`, `abc`, `contextlib`, `io` (StringIO/BytesIO only), `html` |

**Scientific / ML (if installed):**

| Category | Modules |
|----------|---------|
| Numerical computing | `numpy`, `scipy`, `sympy`, `mpmath` |
| Data analysis | `pandas` |
| Machine learning | `sklearn` / `scikit_learn`, `torch`, `torchvision`, `torchaudio`, `tensorflow`, `keras`, `jax`, `flax` |
| Visualization | `matplotlib`, `seaborn`, `plotly` |
| Specialized math | `hypercomplex` |
| Graphs & networks | `networkx`, `igraph` |
| Computer vision | `PIL` / `pillow`, `cv2` |
| Domain-specific | `astropy`, `Bio` / `biopython`, `shapely`, `pyproj`, `pint`, `uncertainties`, `regex` |

### Blocked modules

The following are always blocked at both AST analysis and runtime:

`os`, `sys`, `subprocess`, `shutil`, `pathlib`, `socket`, `http`, `urllib`,
`requests`, `httpx`, `ctypes`, `multiprocessing`, `threading`, `signal`,
`importlib`, `pickle`, `shelve`, `marshal`, `code`, `codeop`, `compileall`,
`py_compile`, `webbrowser`, `antigravity`, `turtle`, `tkinter`, `xml`, `lxml`,
`resource`, `gc`, `inspect`, `dis`, `pty`, `fcntl`, `termios`, `tty`,
`select`, `selectors`, `mmap`, `asyncio`, `concurrent`

### Examples

**Simple calculation:**
```
code: "import math\nmath.sqrt(144)"
→ Result: 12.0
```

**Data analysis:**
```
code: """
data = [23, 45, 67, 12, 89, 34, 56]
import statistics
print(f"Mean: {statistics.mean(data):.1f}")
print(f"Stdev: {statistics.stdev(data):.1f}")
sorted(data)
"""
→ Output:
  Mean: 46.6
  Stdev: 25.7

  Result: [12, 23, 34, 45, 56, 67, 89]
```

**Machine learning (requires torch):**
```
code: """
import torch
x = torch.randn(3, 3)
eigenvalues = torch.linalg.eigvalsh(x @ x.T)
eigenvalues
"""
→ Result: tensor([0.0234, 1.2456, 5.6789])
```

---

## Install Python Package

**Provider:** `python_exec` · **Module:** `tools_python.py` · **Requires:** `uv` on PATH

Request installation of a Python package from PyPI. The user is prompted to approve before installation proceeds. Use this when `execute_python` fails due to a missing library.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `package_name` | string | yes | — | PyPI package name (e.g. `numpy`, `hypercomplex`, `torch`) |
| `reason` | string | yes | — | Explanation of why the package is needed (shown to user) |

### Behavior

1. **Validation** — package name is checked against a strict regex (`^[A-Za-z0-9]([A-Za-z0-9._-]*[A-Za-z0-9])?$`) to prevent command injection
2. **Already-installed check** — attempts `import <package>` in a subprocess; returns immediately if it succeeds
3. **User approval** — emits a `user_input_request` event (same mechanism as `ask_user`); the frontend shows a prompt with the package name, reason, and the exact `uv pip install` command that will run
4. **Installation** — on user approval ("yes"/"y"/"approve"/"ok"), runs `uv pip install <package>` with a 2-minute timeout
5. **Result** — returns success or failure as a ToolResult

### Security

- Package names with shell metacharacters (`;`, `&&`, `$()`, backticks, spaces, slashes) are rejected before reaching the shell
- The user must explicitly approve each installation — AI participants cannot install packages silently
- Installation uses `uv pip install` (never `pip` directly, per project convention)

---

## Document Tools

**Provider:** `documents` · **Module:** `tools_document.py` · **Requires:** `uv pip install -e ".[memory]"` + Ollama embedding service

Tools for ingesting, navigating, and querying reference documents during discussions.

| Tool | Description | Key Parameters |
|------|-------------|----------------|
| `doc_add` | Add a document by URL for analysis | `url`, `title?` |
| `doc_list` | List documents in the current discussion | `full_library?` (search all discussions) |
| `doc_get_length` | Get character count of a document | `document_id` |
| `doc_get_text` | Get a slice of document text by character range | `document_id`, `start`, `end` |
| `doc_get_sections` | Get section headers with character offsets | `document_id` |
| `doc_get_chapter` | Get full text of a named section | `document_id`, `section_name` |
| `doc_ask` | RAG-based Q&A: retrieve relevant chunks, LLM-generated answer with citations | `document_id`, `question` |
| `doc_summary` | Map-reduce summarization of a document or range | `document_id`, `start?`, `end?` |

Supports PDF (via pdfplumber), HTML (via trafilatura), and plain text/Markdown.

---

## Image Tools

**Provider:** `images` · **Module:** `tools_image.py` · **Optional:** Pillow (`[images]` extra)

Tools for working with images in discussions. Vision-capable models receive images as multimodal content blocks automatically; non-vision models use `describe_image` to get text descriptions.

| Tool | Description | Key Parameters |
|------|-------------|----------------|
| `describe_image` | Get a detailed description using a vision model | `image_id`, `question?` |
| `list_images` | List all images attached to the discussion | — |
| `add_image_url` | Add an image from a URL to the discussion | `url`, `title?` |

Security: path traversal protection, SSRF protection (blocks private IPs), 20 MB size limit, MIME validation, auto-resize for large images.

---

## Memory Tools

**Provider:** `memory` · **Module:** `tools_memory.py` · **Requires:** `uv pip install -e ".[memory]"` + Ollama embedding service

Long-term personal memory and knowledge graph for AI participants. Each entity maintains its own private memory, scoped by entity ID.

| Tool | Description | Key Parameters |
|------|-------------|----------------|
| `memory_store` | Store an observation, position, or insight | `content` |
| `memory_recall` | Semantic search over personal memories | `query`, `limit?` |
| `memory_forget` | Delete a specific memory by ID | `memory_id` |
| `discussion_search` | Semantic search across all past discussion messages | `query`, `limit?`, `topic_filter?` |
| `kg_assert` | Assert a knowledge triple (subject → relation → object) | `subject`, `relation`, `object`, `description?` |
| `kg_query` | Query the knowledge graph | `query`, `mode` ("search", "neighbors", "path") |

Default prompt templates encourage AI participants to recall past context before responding and store key insights after contributing.

---

## Tool Assignment

Tools are assigned to AI entities via the **Profiles** tab in the UI. Each assignment has an **access mode**:

| Mode | Behavior |
|------|----------|
| `private` | Only the assigned entity can use the tool |
| `shared` | All entities in the discussion can use the tool |
| `moderator_only` | Only the moderator can use the tool |

Per-discussion overrides can disable specific tools for a particular discussion.

### Always-available providers

| Provider | Tools | Dependencies |
|----------|-------|-------------|
| `builtin` | `web_search`, `fetch_webpage`, `consult_expert` | None (DuckDuckGo fallback); `BRAVE_SEARCH_API_KEY` for Brave Search; MCP server for experts |
| `interactive` | `ask_user` | None |
| `python_exec` | `execute_python`, `install_python_package` | None (`uv` on PATH for package install) |

### Optional providers

| Provider | Tools | Dependencies |
|----------|-------|-------------|
| `documents` | `doc_add`, `doc_list`, `doc_get_length`, `doc_get_text`, `doc_get_sections`, `doc_get_chapter`, `doc_ask`, `doc_summary` | `uv pip install -e ".[memory]"` + Ollama |
| `images` | `describe_image`, `list_images`, `add_image_url` | Pillow (included in `[all]`) |
| `memory` | `memory_store`, `memory_recall`, `memory_forget`, `discussion_search`, `kg_assert`, `kg_query` | `uv pip install -e ".[memory]"` + Ollama |
| MCP servers | Discovered dynamically | Per-server configuration |
