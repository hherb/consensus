# Chapter 2: Providers and Models

Before you can create AI participants, you need to configure at least one AI provider — the API endpoint that Consensus will call to generate responses.

## What is a Provider?

A provider is any service offering an OpenAI-compatible chat completions API. This includes:

- **OpenAI** — GPT-4o, o1, o3, etc.
- **Anthropic** (via proxy or compatible endpoint) — Claude models
- **Google** (via compatible endpoint) — Gemini models
- **OpenRouter** — aggregator providing access to hundreds of models
- **Local servers** — Ollama, LM Studio, llama.cpp, vLLM, etc.
- **Any OpenAI-compatible API** — Groq, Together, Mistral, DeepSeek, etc.

## Configuring a Provider

Navigate to the **Providers** tab in the setup interface.

### Adding a Provider

1. Click **"+ Add Provider"**
2. Fill in the form:

| Field | Description | Example |
|-------|-------------|---------|
| **Name** | A friendly name for this provider | "OpenAI", "Local Ollama" |
| **Base URL** | The API base URL (without `/chat/completions`) | `https://api.openai.com/v1` |
| **API Key Variable** | The environment variable name that holds the key | `OPENAI_API_KEY` |
| **API Key** | The actual API key value | `sk-...` |

3. Click **Save**

### Common Provider Configurations

**OpenAI:**
- Base URL: `https://api.openai.com/v1`
- Key Variable: `OPENAI_API_KEY`

**Anthropic (via OpenRouter):**
- Base URL: `https://openrouter.ai/api/v1`
- Key Variable: `OPENROUTER_API_KEY`

**Ollama (local):**
- Base URL: `http://localhost:11434/v1`
- Key Variable: leave empty (Ollama doesn't require a key)

**LM Studio (local):**
- Base URL: `http://localhost:1234/v1`
- Key Variable: leave empty

**DeepSeek:**
- Base URL: `https://api.deepseek.com/v1`
- Key Variable: `DEEPSEEK_API_KEY`

## API Key Storage

How your API key is stored depends on the mode:

- **Desktop mode:** Keys are saved to `~/.consensus/.env` with restricted file permissions (0600). They persist across sessions.
- **Web mode (BYOK):** Click the **"Set Key"** button next to any provider to enter your key. It is stored in your browser's `sessionStorage` only — it is never sent to the server for storage, and disappears when you close the tab.
- **Environment variables:** You can also set keys as environment variables before launching. The variable name you configure in the provider form tells Consensus where to look.

## Model Discovery

Once a provider is saved, its models become available when creating AI profiles. Consensus queries the provider's model list endpoint and presents them in a dropdown. You can also type a model name manually if it doesn't appear in the list.

## Editing and Deleting Providers

- Click on any provider in the list to edit its settings
- Use the **Delete** button to remove a provider (this does not delete entities that reference it — they will simply fail to generate until reassigned)

## Next Steps

With a provider configured, you're ready to create AI participant profiles. Continue to [Chapter 3: Profiles and Entities](03_profiles_and_entities.md).
