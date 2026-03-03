# Quickstart

Get Consensus running in under five minutes.

## Prerequisites

- **Python 3.11+**
- **[uv](https://docs.astral.sh/uv/getting-started/installation/)** — fast Python package manager
- An OpenAI-compatible API endpoint (OpenAI, Ollama, LMStudio, vLLM, etc.)

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

**Options:**

| Flag        | Default       | Description                    |
|-------------|---------------|--------------------------------|
| `--web`     | off           | Run as web server              |
| `--host`    | `127.0.0.1`   | Bind address for web mode      |
| `--port`    | `8080`        | Port for web mode              |
| `--debug`   | off           | Enable debug logging           |

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

### 4. Discuss

- **AI participants** generate responses automatically when it's their turn
- **Human participants** type messages when prompted
- The **moderator** summarizes after each turn and can mediate conflicts
- Click **Conclude** to end the discussion and generate a final synthesis

## Tips

- The moderator can be either human or AI. An AI moderator generates summaries automatically; a human moderator is prompted to type summaries.
- Check the **Storyboard** panel (right side) for a running summary of the discussion.
- You can **reassign turns** to any participant at any time.
- The **Prompts** tab lets you customize the system prompts and instructions for AI moderators and participants.
- Past discussions are saved and can be reviewed from the **History** tab.

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

**Blank window in desktop mode** — Try web mode instead, or run with `--debug` to see errors.
