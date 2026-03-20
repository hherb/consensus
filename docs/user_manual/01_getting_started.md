# Chapter 1: Getting Started

## Requirements

- Python 3.10 or later
- [uv](https://docs.astral.sh/uv/) package manager (recommended over pip)
- At least one AI provider API key (OpenAI, Anthropic, a local Ollama instance, or any OpenAI-compatible endpoint)

## Installation

Consensus offers several installation extras depending on how you plan to use it.

### As a CLI tool (recommended for users)

```bash
# Install with all extras — makes the `consensus` command available globally
uv tool install ".[all]"
```

You can also install with only the extras you need:

```bash
uv tool install ".[desktop]"   # desktop mode only
uv tool install ".[web]"       # web server mode only
```

### For development

```bash
# Editable install into the current virtual environment
uv pip install -e ".[all]"
```

> **Note:** Always use `uv`, not plain `pip`. The project is configured for uv-based dependency management.

## Launch Modes

### Desktop Mode (default)

```bash
consensus
# or
python -m consensus
```

Opens a native application window using pywebview. This is the simplest way to use Consensus for personal use — no browser required, no server to manage.

### Web Server Mode (single user)

```bash
consensus --web
# or with options
consensus --web --port 8080 --host 127.0.0.1 --debug
```

Starts an aiohttp web server. Open your browser to `http://127.0.0.1:8080`. All browser tabs share the same application instance.

### Multi-User Mode

```bash
consensus --web --multi-user
```

Each browser session gets its own isolated application instance and database. Intended for shared or public deployments. See [Chapter 14](14_multi_user_and_auth.md) for authentication setup.

### MCP Server Mode

```bash
consensus-mcp
```

Runs Consensus as an MCP (Model Context Protocol) server over stdio, allowing external AI agents like Claude Code to drive discussions programmatically. See [Chapter 15](15_mcp_integration.md).

## Command-Line Options

| Option | Description |
|--------|-------------|
| `--web` | Run as a web server instead of desktop |
| `--multi-user` | Enable per-session isolation (requires `--web`) |
| `--port PORT` | Server port (default: 8080) |
| `--host HOST` | Bind address (default: 127.0.0.1) |
| `--debug` | Enable debug logging and auto-reload |

## Data Storage

Consensus stores its data in platform-standard locations:

| Platform | Location |
|----------|----------|
| macOS | `~/Library/Application Support/consensus/` |
| Linux | `~/.local/share/consensus/` (or `$XDG_DATA_HOME/consensus/`) |
| Windows | `%APPDATA%/consensus/` |

This directory contains:
- The SQLite database (`consensus.db`)
- Uploaded images (`images/` subdirectory)
- Migration history

API keys are stored separately in `~/.consensus/.env` with restricted file permissions (0600).

## First Run

When you first launch Consensus, the application:

1. Creates the database with all required tables
2. Seeds default prompt templates for moderator and participant roles
3. Opens the setup interface

You will see a tabbed interface with: **New Discussion**, **Providers**, **Profiles**, **Prompts**, **Memory**, and **History**.

Your first step should be configuring at least one AI provider. Continue to [Chapter 2: Providers and Models](02_providers_and_models.md).
