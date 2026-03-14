# Quickstart

Get Consensus running in under five minutes.

## Prerequisites

- **Python 3.11+**
- **[uv](https://docs.astral.sh/uv/getting-started/installation/)** — fast Python package manager
- An OpenAI-compatible API endpoint (OpenAI, Ollama, LMStudio, vLLM, etc.)

### Linux system dependencies (desktop mode only)

Desktop mode uses [pywebview](https://pywebview.flowrl.com/), which requires GTK or QT. On Debian/Ubuntu:

```bash
sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-3.0 gir1.2-webkit2-4.1
```

> **Headless servers (DGX, cloud VMs, WSL without GUI):** Skip these dependencies and use **web mode** instead (`consensus --web`). No system GUI libraries are needed for web mode.

If you want to use local AI models, install [Ollama](https://ollama.com) and pull a model:

```bash
ollama pull llama3
```

## Install

Clone the repository and install with uv:

```bash
git clone https://github.com/hherb/consensus.git
cd consensus

# Install as a global command (both desktop + web modes)
uv tool install -e ".[all]"
```

This places the `consensus` command in `~/.local/bin/` so it works from anywhere. The `-e` flag keeps it editable — source changes take effect immediately.

> **Pick a single mode instead:**
> ```bash
> uv tool install -e ".[desktop]"   # desktop only
> uv tool install -e ".[web]"       # web server only
> ```

## Run

### Desktop mode (default)

```bash
consensus
```

A native window opens with the full UI.

### Web mode

```bash
consensus --web
consensus --web --port 9090
```

Then open http://127.0.0.1:8080 (or your chosen port) in your browser.

### Multi-user mode (public deployment)

```bash
consensus --web --multi-user
consensus --web --multi-user --host 0.0.0.0 --port 8080
```

Each browser session gets its own isolated app instance and database. Users provide their own API keys via the browser UI (BYOK).

**Options:**

| Flag            | Default       | Description                                    |
|-----------------|---------------|------------------------------------------------|
| `--web`         | off           | Run as web server                              |
| `--host`        | `127.0.0.1`  | Bind address for web mode                      |
| `--port`        | `8080`        | Port for web mode                              |
| `--multi-user`  | off           | Per-session isolation for public deployment    |
| `--debug`       | off           | Enable debug logging                           |

## First-time setup

The app creates a SQLite database on first run. No manual database setup is needed.

**Database location:**
- macOS: `~/Library/Application Support/consensus/consensus.db`
- Linux: `~/.local/share/consensus/consensus.db`
- Windows: `%APPDATA%/consensus/consensus.db`

### 1. Add a provider

Go to **Providers** tab and add an API endpoint:

| Field       | Example (Ollama)               | Example (OpenAI)                    |
|-------------|--------------------------------|-------------------------------------|
| Name        | `Ollama Local`                 | `OpenAI`                            |
| Base URL    | `http://localhost:11434/v1`    | `https://api.openai.com/v1`         |
| API Key Env | *(leave empty)*                | `OPENAI_API_KEY`                    |

The **API Key Env** field is the name of an environment variable containing your API key. Set it before launching:

```bash
export OPENAI_API_KEY="sk-..."
consensus --web
```

### 2. Create entity profiles

Go to **Profiles** tab and create participants. Each entity is either **Human** or **AI**.

For AI entities, select the provider you created and specify the model name (e.g. `llama3`, `gpt-4o`).

### 3. Set up a discussion

Go to **New Discussion** tab:

1. Add at least 2 entities to the discussion
2. Designate one as **moderator** (click "Set Mod")
3. Enter a discussion topic
4. Click **Start Discussion**

### 3b. Choose a discussion method (optional)

The default is **Open Discussion** (freeform turn-taking). Other structured methods are available in the setup panel:

| Method | Purpose |
|--------|---------|
| **Delphi** | Anonymous multi-round estimation with convergence |
| **Red Team / Blue Team** | Adversarial stress-testing of proposals |
| **Premortem** | "Assume this failed — why?" risk analysis |
| **ACH** | Analysis of Competing Hypotheses |
| **Key Assumptions Check** | Surface and challenge hidden assumptions |
| **Adversarial Collaboration** | Structured disagreement toward resolution |
| **Belief Diffusion** | Track how opinions shift through discussion |
| **Voting** | Formal vote to reach a decision |

### 3c. Attach images or documents (optional)

- **Images:** Upload or paste a URL in the Images panel. Vision-capable models see them directly; others can use the `describe_image` tool.
- **Documents:** Upload PDFs, HTML, Markdown, or plain text. AI participants can search, quote, and answer questions about document content via RAG tools.

### 4. Discuss

- **AI participants** generate responses automatically when it's their turn
- **Human participants** type messages when prompted
- The **moderator** summarizes after each turn and can mediate conflicts
- **Pause** the discussion at any time and **resume** it later
- **Add or remove participants** mid-discussion
- Click **Conclude** to end the discussion and generate a final synthesis
- **Resume** a concluded discussion to continue the conversation

## AI Tools

AI participants can use tools during their turns. Assign tools per-entity in the **Profiles** tab under the Tools section.

### Built-in tools

| Tool | Requires | Description |
|------|----------|-------------|
| **web_search** | `BRAVE_API_KEY` env var (falls back to DuckDuckGo) | Search the web for current information |
| **fetch_webpage** | — | Extract content from a URL |
| **describe_image** | A vision-capable model in the discussion | Get AI-generated description of an attached image |
| **list_images** | — | List images attached to the discussion |
| **add_image_url** | — | Add an image from a URL mid-discussion |
| **ask_user** | A human user present | Pause and request input from the human user mid-turn |
| **add_document** / **ask_document** | — | Ingest and query reference documents via RAG |
| **consult_expert** | An expert entity configured | Get specialist analysis from an expert entity |

Memory and knowledge-graph tools are covered in the next section.

### MCP servers (external tools)

Connect [Model Context Protocol](https://modelcontextprotocol.io/) servers to give AI participants access to external capabilities (databases, APIs, code execution, etc.).

1. Go to **Providers → MCP Servers**
2. Add a server (stdio command or HTTP URL)
3. Click **Test** to verify connectivity
4. Assign MCP tools to entities in the **Profiles** tab

MCP servers can also be auto-loaded from a config file. The app searches these paths on startup:
- `./mcp_servers.json` (current directory)
- `~/.consensus/mcp_servers.json`
- Platform data dir (e.g. `~/Library/Application Support/consensus/mcp_servers.json` on macOS)

## Exporting Discussions

From the discussion view or the **History** tab, export in three formats:

- **JSON** — Structured data with full metadata, tool calls, and storyboard
- **HTML** — Self-contained styled document (dark/light mode aware)
- **PDF** — Opens the HTML export in a print dialog

Exports include per-message AI metadata (model, tokens, latency, cost) and tool call records.

## Cost Tracking

Message costs are calculated automatically using OpenRouter pricing data. Per-message costs appear in the discussion view, and total cost is shown in exports. The pricing cache refreshes weekly.

## Enabling Institutional Memory (Optional)

AI participants can remember insights across discussions using persistent memory, semantic search, and a knowledge graph. This requires an embedding model.

### 1. Install memory extras

```bash
uv tool install -e ".[all]"       # includes memory deps
# or just the memory extras:
uv pip install -e ".[memory]"
```

### 2. Set up an embedding model

Memory uses [Ollama](https://ollama.com) for text embeddings. Install Ollama and pull an embedding model:

```bash
ollama pull nomic-embed-text-v2-moe
```

### 3. Assign memory tools to entities

Go to the **Profiles** tab, select an AI entity, and enable the memory tools in the Tools section:
- **memory_store** / **memory_recall** / **memory_forget** — personal long-term memory
- **discussion_search** — semantic search across all past discussions
- **kg_assert** / **kg_query** — knowledge graph (concept relationships)

### 4. How models use memory

Models **choose autonomously** when to use memory tools — no manual prompting needed. The default prompt templates encourage proactive memory use:
- Before responding, models check for relevant memories from past discussions
- After contributing, they store key insights for future reference
- They build a knowledge graph of conceptual relationships as they discuss

You can configure the embedding endpoint and model in the **Settings** tab under "Memory Configuration".

## Authentication (multi-user mode)

When running with `--multi-user`, users must register and log in:

- **Email/password** registration with secure PBKDF2-SHA256 hashing
- **OAuth** sign-in via GitHub, Google, LinkedIn, or Apple (configure via environment variables — see `docs/devel/programmer-manual.md`)
- Each user gets an isolated database — no data is shared between users
- **BYOK:** Users provide their own API keys via the browser UI. Keys are stored in `sessionStorage` and sent per-request — never persisted on the server.

## Tips

- The moderator can be either human or AI. An AI moderator generates summaries automatically; a human moderator is prompted to type summaries.
- Check the **Storyboard** panel (right side) for a running summary of the discussion.
- You can **reassign turns** to any participant at any time.
- **Export** discussions as JSON, HTML, or PDF from the discussion view or History tab.
- The **Prompts** tab lets you customize the system prompts and instructions for AI moderators and participants.
- Past discussions are saved and can be reviewed from the **History** tab.
- Discussion methods can be changed per-discussion — experiment with Delphi for estimation tasks, Red Team for stress-testing proposals, etc.

## Troubleshooting

**"Web mode requires aiohttp"** — You installed without the `web` extra:
```bash
uv tool install -e ".[web]"    # or ".[all]" for both modes
```

**"Desktop mode requires pywebview"** — You installed without the `desktop` extra:
```bash
uv tool install -e ".[desktop]"    # or ".[all]" for both modes
```

**AI responses fail** — Verify your provider is running and reachable. For Ollama:
```bash
curl http://localhost:11434/v1/models
```

**"You must have either QT or GTK" / "No module named 'gi'"** — Desktop mode needs GTK or QT system libraries. Install them (see [Prerequisites](#linux-system-dependencies-desktop-mode-only)) or use web mode:
```bash
consensus --web
```

**Headless server / no display** — On servers without a GUI (DGX, cloud VMs, WSL), always use web mode:
```bash
consensus --web --host 0.0.0.0
```

**"Failed to fetch pricing from OpenRouter"** — Non-fatal warning. Pricing data is used for cost tracking only. Check your network/DNS if you need cost estimates.

**Blank window in desktop mode** — Try web mode instead, or run with `--debug` to see errors.
