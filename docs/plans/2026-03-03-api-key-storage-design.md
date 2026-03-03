# API Key Storage Design

## Problem

API keys are currently stored in the SQLite `providers.api_key_env` field — either as env var names or literal keys. This is insecure (keys visible in DB), confusing (field serves dual purpose), and the UI displays keys in cleartext.

## Design

### Storage Location

`~/.consensus/.env` — a standard dotenv file with `0600` permissions. Created on first key save. Loaded on app startup via `python-dotenv`.

### Startup Flow

`config.py` gains a `load_env()` function called early in app init:
1. Check `~/.consensus/.env` exists
2. Load with `dotenv.load_dotenv(override=False)` — real env vars take precedence
3. This runs before `Database` or `ConsensusApp` init

### Database Changes

`providers.api_key_env` stores only env var names (e.g. `ANTHROPIC_API_KEY`), never literal keys. The `resolve_api_key()` helper simplifies to `os.environ.get()`.

### Backend API Changes

- `add_provider` / `update_provider`: Accept an optional `api_key` param. If provided, write `{api_key_env}={api_key}` to `~/.consensus/.env` and store only the env var name in the DB.
- `get_providers` / `get_state`: Return `has_key: bool` per provider instead of the actual key value. Never send keys to the frontend.
- New config helper: `save_api_key(env_var_name, key_value)` — reads existing `.env`, updates the line, writes back with `0600` perms. Also sets `os.environ[env_var_name]` so it takes effect immediately.

### Frontend Changes

- Provider list shows "Configured" / "Not set" instead of the key value.
- Provider dialog has an API Key field with placeholder "Enter API key to update". When editing an existing provider with a key, the field is left blank (not pre-filled) with a hint that leaving it blank keeps the current key.
- Clearing the field and saving removes the key.

### Migration

On startup, `_migrate_providers()` detects literal keys in `api_key_env` (values that don't look like env var names — contain dashes, dots, or are longer than typical var names). Moves them to `~/.consensus/.env` under the conventional name derived from the provider name (e.g. "Anthropic" -> `ANTHROPIC_API_KEY`), then updates the DB field to the env var name.

### Dependency

Add `python-dotenv` to base dependencies in `pyproject.toml`.

## Files to Modify

1. `pyproject.toml` — add python-dotenv dependency
2. `consensus/config.py` — add `load_env()`, `save_api_key()`, `remove_api_key()`, `get_env_path()`
3. `consensus/models.py` — simplify `resolve_api_key()` back to env-only lookup
4. `consensus/app.py` — update provider CRUD to handle key save/redaction
5. `consensus/database.py` — update migration to move literal keys to .env
6. `consensus/desktop.py` — pass through new api_key param
7. `consensus/server.py` — pass through new api_key param
8. `consensus/static/app.js` — update provider UI (has_key display, key input flow)
9. `consensus/static/index.html` — update provider dialog label/hint
10. `consensus/__main__.py` — call `load_env()` before app init
