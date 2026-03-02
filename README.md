# Consensus

A moderated discussion platform where two or more entities (humans and/or AI via OpenAI-compatible APIs) can discuss topics with a designated moderator who arbitrates, summarizes, and synthesizes conclusions. The moderator can be AI or human and may participate in the dicussion

<p align="center">
  <img src="assets/consensus.png" alt="Consensus" width="200">
</p>

## Features

- **Turn-based discussions** between any mix of human and AI participants
- **Designated moderator** (human or AI) that summarizes after each turn, mediates conflicts, and produces final synthesis
- **Storyboard** showing running summaries alongside the conversation
- **OpenAI-compatible API** support (OpenAI, Ollama, LMStudio, vLLM, etc.)
- **Desktop mode** via pywebview - lightweight native window
- **Web mode** via aiohttp - accessible from any browser/mobile device
- **Dark/light theme** with responsive layout

## Installation

```bash
# Core only (library use)
pip install .

# Desktop mode (recommended)
pip install ".[desktop]"

# Web server mode
pip install ".[web]"

# Everything
pip install ".[all]"
```

## Usage

```bash
# Desktop mode (default)
python -m consensus

# Web server mode (accessible from browser/mobile)
python -m consensus --web
python -m consensus --web --port 8080
```

## Architecture

- **httpx** - async HTTP client for OpenAI-compatible API calls
- **pywebview** - lightweight cross-platform desktop webview (BSD-3)
- **aiohttp** - optional web server for browser/mobile access (BSD-3)
- **Pure HTML/CSS/JS** frontend - no framework dependencies

## License

AGPL-3.0 - see [LICENSE](LICENSE)
