# Consensus

A moderated discussion platform where two or more entities (humans and/or AI via OpenAI-compatible APIs) can discuss topics with a designated moderator who arbitrates, summarizes, and synthesizes conclusions. The moderator can be AI or human and may participate in the discussion.

<p align="center">
  <img src="assets/consensus.png" alt="Consensus">
</p>

## Features

### Discussion Engine
- **Turn-based discussions** between any mix of human and AI participants
- **Designated moderator** (human or AI) that summarizes after each turn, mediates conflicts, and produces final synthesis
- **Automatic turn rotation** with manual reassignment option
- **Storyboard panel** showing running summaries and conclusions alongside the conversation
- **AI-to-AI conversations** run automatically without manual intervention
- **Context-aware AI responses** with configurable context window (last N messages)

### Multi-Provider AI Support
- **OpenAI-compatible API** support — works with OpenAI, Anthropic, Ollama, DeepSeek, LMStudio, vLLM, and any compatible endpoint
- **Provider registry** with pre-seeded defaults (Ollama, Anthropic, DeepSeek, OpenAI)
- **Dynamic model discovery** — automatically fetches available models from each provider
- **Per-entity configuration** — temperature, max tokens, and custom system prompts per participant
- **Secure API key handling** — keys referenced by environment variable name, never stored on disk

### Prompt Template System
- **Customizable prompt templates** for every AI task (turn generation, summarization, mediation, conclusion, opening)
- **Role-aware templates** — separate templates for moderator vs participant, AI vs human
- **Template variables** — `{entity_name}`, `{topic}`, `{participants}`, `{speaker_name}`, `{turn_number}`, `{context}`
- **Default templates** seeded on first run, fully editable

### Entity Profiles
- **Reusable participant profiles** with name, type (human/AI), and avatar color
- **AI configuration per profile** — provider, model, temperature, max tokens, system prompt
- **Color-coded avatars** with 8 presets or custom hex colors

### Persistence & History
- **SQLite database** with thread-safe concurrent access (WAL mode)
- **Platform-aware storage** — macOS: `~/Library/Application Support/consensus/`, Linux: `~/.local/share/consensus/`, Windows: `%APPDATA%/consensus/`
- **Full discussion history** — browse, load, and review past discussions
- **Message metadata** — model name, token counts, latency tracking per AI response

### User Interface
- **Tabbed setup** — New Discussion, Providers, Profiles, Prompts, History
- **Three-panel discussion view** — participants sidebar, chat center, storyboard sidebar
- **Dark/light theme** with automatic system preference detection
- **Markdown rendering** in messages (headers, bold, italic, code blocks, lists)
- **Toast notifications** with auto-dismiss
- **Speaking indicator** animation for active participant
- **Pure HTML/CSS/JS frontend** — no framework dependencies

### Dual-Mode Application
- **Desktop mode** via pywebview — lightweight native window (1280x800 default, 900x600 minimum)
- **Web mode** via aiohttp — accessible from any browser or mobile device
- Both modes share the same backend and feature set

## Installation

Consensus uses [uv](https://docs.astral.sh/uv/) for dependency management (though pip works too).

```bash
# Using uv (recommended)
uv sync                        # core dependencies
uv sync --extra desktop        # + pywebview for desktop mode
uv sync --extra web            # + aiohttp for web server mode
uv sync --extra all            # everything

# Using pip
pip install .                  # core only
pip install ".[desktop]"       # desktop mode
pip install ".[web]"           # web server mode
pip install ".[all]"           # everything
```

Requires Python 3.11 or later.

## Usage

```bash
# Desktop mode (default)
python -m consensus
# or
consensus

# Web server mode (accessible from browser/mobile)
python -m consensus --web
python -m consensus --web --host 0.0.0.0 --port 8080

# Debug mode
python -m consensus --web --debug
```

### Setting Up AI Providers

API keys are configured via environment variables. Set the relevant variables before launching:

```bash
export OPENAI_API_KEY="sk-..."
export ANTHROPIC_API_KEY="sk-ant-..."
export DEEPSEEK_API_KEY="sk-..."
```

For local providers like Ollama, no API key is needed — just ensure the service is running.

## Architecture

```
Frontend (static HTML/CSS/JS)
    ↕ pywebview bridge OR aiohttp REST API
ConsensusApp — orchestrator, state management
    ├── Moderator — turn flow, AI generation, summaries
    ├── AIClient — async OpenAI-compatible HTTP client (httpx)
    └── Database — thread-safe SQLite persistence
```

**Key dependencies:**
- **httpx** — async HTTP client for OpenAI-compatible API calls
- **pywebview** — lightweight cross-platform desktop webview (optional)
- **aiohttp** — web server for browser/mobile access (optional)

## License

AGPL-3.0 — see [LICENSE](LICENSE)
