# 9. Prompts, Providers, and Security

[Back to index](programmer-manual.md) | [Previous: Tool Use](08-tool-use.md) | [Next: Contributing](10-contributing.md)

---

## The Prompt Template System

Prompts are stored in the `prompts` table and categorised by three axes:

| Axis | Values | Meaning |
|------|--------|---------|
| `role` | `moderator`, `participant` | Who uses this prompt |
| `target` | `ai`, `human` | Is the user AI or human? |
| `task` | `system`, `turn`, `summarize`, `mediate`, `conclude`, `open`, `guidance` | What the prompt is for |

### Default prompts seeded on first run (9 total)

| Name | Role | Target | Task |
|------|------|--------|------|
| AI Moderator -- System | moderator | ai | system |
| AI Moderator -- Summarize | moderator | ai | summarize |
| AI Moderator -- Mediate | moderator | ai | mediate |
| AI Moderator -- Conclude | moderator | ai | conclude |
| AI Moderator -- Open | moderator | ai | open |
| AI Participant -- System | participant | ai | system |
| AI Participant -- Turn | participant | ai | turn |
| Human Moderator -- Guidance | moderator | human | guidance |
| Human Participant -- Guidance | participant | human | guidance |

### Template variables

Replaced by `Moderator.resolve_prompt()`:

| Variable | Replaced with |
|----------|--------------|
| `{entity_name}` | The speaking entity's name |
| `{topic}` | The discussion topic |
| `{participants}` | Comma-separated list: "Alice (Human), GPT-4 (AI)" |
| `{speaker_name}` | The entity who just spoke (for summaries) |
| `{turn_number}` | Current turn number |
| `{context}` | Additional context (for mediation) |

### Prompt priority

If an entity has a custom `system_prompt` set in its profile, that overrides
the database template for the system prompt. Task prompts (turn, summarize,
etc.) always come from the database.

### How prompts are selected

`Moderator.resolve_prompt(role, target, task)` calls
`Database.get_prompt_by_task()`, which returns the first match ordered by
`is_default DESC`. Default prompts are preferred but user-created prompts for
the same role/target/task combination will be used if they exist.

---

## AI Provider Integration

The system is designed around the **OpenAI chat completions API** as a common
protocol. Any LLM server that implements `/v1/chat/completions` and
`/v1/models` works.

### Provider registry

Providers are stored in the `providers` table:
```
id | name            | base_url                      | api_key_env
---+-----------------+-------------------------------+------------------
1  | Ollama (Local)  | http://localhost:11434/v1      |
2  | Anthropic       | https://api.anthropic.com/v1   | ANTHROPIC_API_KEY
3  | OpenAI          | https://api.openai.com/v1      | OPENAI_API_KEY
4  | DeepSeek        | https://api.deepseek.com       | DEEPSEEK_API_KEY
5  | Mistral         | https://api.mistral.ai/v1      | MISTRAL_API_KEY
```

### How an entity connects to a provider

1. Entity profile has a `provider_id` (foreign key to `providers`)
2. When loading an entity, `get_entities()` does a `LEFT JOIN` with
   `providers` to include `base_url` and `api_key_env`
3. `AIConfig.from_db_row()` builds the config, calling
   `resolve_api_key(api_key_env)` to look up the actual key from `os.environ`
4. `Moderator._get_client(entity)` creates an `AIClient(base_url, api_key)`
5. `AIClient.complete()` posts to `{base_url}/chat/completions`

### BYOK (Bring Your Own Key)

In multi-user mode, the entity-to-provider flow has an additional layer:

1. Frontend stores user-provided keys in `sessionStorage`
2. Keys are sent as `X-API-Keys` HTTP header (JSON map: `provider_id -> key`)
3. Server extracts keys via `_extract_api_keys(request)`
4. Keys are set in a `contextvars.ContextVar` (request-scoped)
5. `ConsensusApp.resolve_provider_api_key()` checks BYOK first, falls back to
   environment variables
6. Keys are cleared after the request completes (in `finally` block)

Keys are **never** logged, cached, or written to disk on the server.

### Anthropic special handling

Anthropic's native API uses `x-api-key` header instead of `Authorization:
Bearer`. The `AIClient` detects Anthropic URLs and switches to a separate
`_list_models_anthropic()` method with the correct headers and pagination.

> **Note:** For chat completions, Anthropic is expected to be accessed through
> their OpenAI-compatible proxy, which does use Bearer auth. The special
> handling is only for model listing.

### Adding a new provider type

If a new provider needs special handling beyond the OpenAI-compatible protocol:
1. Add detection logic in `AIClient` (like `_is_anthropic()`)
2. Override the relevant method (`list_models`, `complete`, or `stream`)
3. The rest of the system (prompts, turns, summaries) works unchanged

---

## Security Considerations

These are the security measures currently in place. Contributors should
maintain these invariants:

- **API keys are never stored in the database.** Only environment variable
  names (e.g. `OPENAI_API_KEY`) are persisted. Actual keys live in
  `~/.consensus/.env` with `0600` permissions, or in the process environment.

- **Keys are never sent to the frontend.** `_provider_for_frontend()` strips
  the `api_key_env` field and replaces it with a boolean `has_key`.

- **BYOK keys are never persisted server-side.** In multi-user mode, API keys
  sent via `X-API-Keys` headers are used for the duration of the request and
  never logged, cached, or written to disk.

- **Path traversal protection:** `server.py` resolves static file paths with
  `os.path.realpath()` and checks they start with the static directory prefix.

- **Security headers:** All responses include `X-Content-Type-Options: nosniff`,
  `X-Frame-Options: DENY`, and `Referrer-Policy: strict-origin-when-cross-origin`.

- **CORS origin checking:** The web server middleware rejects API requests from
  non-matching `Origin` headers. In multi-user mode, allowed origins must be
  explicitly configured via `CONSENSUS_ALLOWED_ORIGINS`.

- **Rate limiting:** Per-session/IP rate limiting prevents abuse (120
  requests per 60-second window by default).

- **Session security:** Session IDs are cryptographically random
  (`secrets.token_urlsafe(32)`), validated against a strict regex, and set as
  httponly cookies with `SameSite=Lax`. Sessions expire after 24h of
  inactivity.

- **HTML escaping:** `renderMarkdown()` in `app.js` escapes HTML entities
  before applying Markdown formatting, preventing XSS from message content.

- **SQL injection prevention:** All database queries use parameterised
  statements (`?` placeholders). The one dynamic table name in `_update_row()`
  is validated against a whitelist (`_VALID_TABLES`).

- **Tool execution sandboxing:** Tool calls are subject to a 30-second timeout
  and a maximum of 5 iterations per turn. Tool results are treated as untrusted
  content.

### Authentication security (multi-user mode)

- **Password hashing:** PBKDF2-SHA256 with 600,000 iterations (OWASP 2023
  recommendation) and 32-byte random salts. Verification uses
  `hmac.compare_digest()` for timing-safe comparison.

- **Token storage:** Auth tokens are generated with `secrets.token_urlsafe(32)`
  (256 bits of entropy) and stored as SHA-256 hashes — never plaintext.
  Tokens are set as httpOnly cookies only, never in response bodies.

- **CSRF protection:** A middleware rejects POST requests to `/api/` and
  `/auth/` that lack `Content-Type: application/json`. OAuth callbacks are
  exempt (Apple uses `response_mode=form_post`).

- **Login brute-force protection:** Maximum 5 failed login attempts per email
  per 5-minute window. State is in-memory (resets on server restart).

- **OAuth state tokens:** Cryptographically random, stored in the database
  with a 10-minute TTL, consumed on use (single-use).

- **OAuth redirect URI:** Derived from the `CONSENSUS_BASE_URL` environment
  variable (not request headers) to prevent host header injection attacks.

- **Profile update allowlist:** `POST /auth/me` only accepts `display_name`,
  `avatar_url`, and `email` — all other fields are silently ignored.

- **XSS in OAuth errors:** OAuth error pages use `html.escape()` on
  user-controlled input.

---

[Next: Contributing](10-contributing.md)
