# API Key Storage Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Store API keys securely in `~/.consensus/.env`, never expose them to the frontend, and let users enter keys directly in the provider UI.

**Architecture:** `config.py` gains dotenv helpers (`load_env`, `save_api_key`, `remove_api_key`). On startup the `.env` file is loaded into `os.environ`. The provider CRUD in `app.py` accepts a separate `api_key` param, writes it to `.env`, and stores only the env var name in the DB. `get_providers()` returns `has_key: bool` instead of the raw value. The frontend shows "Configured" / "Not set" and a password-type input that is never pre-filled.

**Tech Stack:** python-dotenv, existing SQLite + httpx + vanilla JS stack

---

### Task 1: Add python-dotenv dependency

**Files:**
- Modify: `pyproject.toml:11-13`

**Step 1: Add python-dotenv to base dependencies**

In `pyproject.toml`, change:

```toml
dependencies = [
    "httpx>=0.27",
]
```

to:

```toml
dependencies = [
    "httpx>=0.27",
    "python-dotenv>=1.0",
]
```

**Step 2: Install the updated package**

Run: `pip install -e ".[all]"`

**Step 3: Verify import**

Run: `python -c "import dotenv; print(dotenv.__version__)"`
Expected: version number printed, no error

**Step 4: Commit**

```
git add pyproject.toml
git commit -m "Add python-dotenv dependency"
```

---

### Task 2: Add dotenv helpers to config.py

**Files:**
- Modify: `consensus/config.py`

**Step 1: Add `get_env_path()`, `load_env()`, `save_api_key()`, `remove_api_key()`**

Replace the full contents of `consensus/config.py` with:

```python
"""Platform-aware configuration and paths."""

import os
import stat
import sys


def get_data_dir() -> str:
    """Get the platform-appropriate data directory."""
    if sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
    elif sys.platform == "win32":
        base = os.environ.get("APPDATA", os.path.expanduser("~"))
    else:
        base = os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share"))
    path = os.path.join(base, "consensus")
    os.makedirs(path, exist_ok=True)
    return path


def get_db_path() -> str:
    """Get the default database file path."""
    return os.path.join(get_data_dir(), "consensus.db")


def get_env_path() -> str:
    """Return the path to ~/.consensus/.env."""
    d = os.path.expanduser("~/.consensus")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, ".env")


def load_env() -> None:
    """Load ~/.consensus/.env into os.environ (existing vars take precedence)."""
    from dotenv import load_dotenv
    path = get_env_path()
    if os.path.isfile(path):
        load_dotenv(path, override=False)


def _read_env_lines(path: str) -> list[str]:
    """Read .env file lines, returning empty list if file doesn't exist."""
    if not os.path.isfile(path):
        return []
    with open(path, "r") as f:
        return f.readlines()


def _write_env(path: str, lines: list[str]) -> None:
    """Write lines to .env file with restrictive permissions."""
    with open(path, "w") as f:
        f.writelines(lines)
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)  # 0600
    except OSError:
        pass  # Windows may not support chmod


def save_api_key(env_var: str, key_value: str) -> None:
    """Write or update an API key in ~/.consensus/.env.

    Also sets the key in os.environ so it takes effect immediately.
    """
    if not env_var or not key_value:
        return
    path = get_env_path()
    lines = _read_env_lines(path)

    # Replace existing line or append
    prefix = f"{env_var}="
    found = False
    new_lines: list[str] = []
    for line in lines:
        if line.startswith(prefix):
            new_lines.append(f"{env_var}={key_value}\n")
            found = True
        else:
            new_lines.append(line)
    if not found:
        new_lines.append(f"{env_var}={key_value}\n")

    _write_env(path, new_lines)
    os.environ[env_var] = key_value


def remove_api_key(env_var: str) -> None:
    """Remove an API key from ~/.consensus/.env and os.environ."""
    if not env_var:
        return
    path = get_env_path()
    lines = _read_env_lines(path)

    prefix = f"{env_var}="
    new_lines = [line for line in lines if not line.startswith(prefix)]
    _write_env(path, new_lines)
    os.environ.pop(env_var, None)


def has_api_key(env_var: str) -> bool:
    """Return True if the env var is set (from .env or real environment)."""
    if not env_var:
        return False
    return bool(os.environ.get(env_var, ""))
```

**Step 2: Verify it works**

Run:
```bash
python -c "
from consensus.config import get_env_path, load_env, save_api_key, remove_api_key, has_api_key
import os

# Save a test key
save_api_key('_CONSENSUS_TEST_KEY', 'test123')
assert os.environ.get('_CONSENSUS_TEST_KEY') == 'test123'
assert has_api_key('_CONSENSUS_TEST_KEY')

# Remove it
remove_api_key('_CONSENSUS_TEST_KEY')
assert not has_api_key('_CONSENSUS_TEST_KEY')

print('config helpers OK')
"
```
Expected: `config helpers OK`

**Step 3: Commit**

```
git add consensus/config.py
git commit -m "Add dotenv helpers for API key storage in ~/.consensus/.env"
```

---

### Task 3: Simplify resolve_api_key and call load_env on startup

**Files:**
- Modify: `consensus/models.py:22-34`
- Modify: `consensus/__main__.py:10-39`
- Modify: `consensus/desktop.py:186-206`
- Modify: `consensus/server.py:17-19`

**Step 1: Simplify resolve_api_key in models.py**

Replace the `resolve_api_key` function (lines 22-34) with:

```python
def resolve_api_key(env_var: str) -> str:
    """Resolve an API key by looking up the env var name in os.environ."""
    if not env_var:
        return ""
    return os.environ.get(env_var, "")
```

**Step 2: Add load_env() call in __main__.py**

In `consensus/__main__.py`, add the import and call `load_env()` at the top of `main()`, before argument parsing:

```python
"""Entry point for the consensus application."""

import argparse
import sys

from .config import load_env

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8080


def main() -> None:
    """Parse command-line arguments and launch desktop or web mode."""
    load_env()

    parser = argparse.ArgumentParser(
        description="Consensus - Moderated Discussion Platform"
    )
    # ... rest unchanged
```

**Step 3: Add load_env() call in desktop.py launch_desktop**

At the top of `launch_desktop()` in `consensus/desktop.py`, add:

```python
def launch_desktop(debug: bool = False) -> None:
    """Launch the desktop application using pywebview."""
    import webview
    from .config import load_env
    load_env()
    # ... rest unchanged
```

**Step 4: Add load_env() call in server.py launch_web**

At the top of `launch_web()` in `consensus/server.py`, add:

```python
async def launch_web(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> None:
    """Start the aiohttp web server and block until interrupted."""
    from .config import load_env
    load_env()
    app = ConsensusApp()
    # ... rest unchanged
```

**Step 5: Verify imports**

Run: `python -c "from consensus.__main__ import main; from consensus.models import resolve_api_key; print('OK')"`
Expected: `OK`

**Step 6: Commit**

```
git add consensus/models.py consensus/__main__.py consensus/desktop.py consensus/server.py
git commit -m "Load dotenv on startup, simplify resolve_api_key to env-only"
```

---

### Task 4: Update app.py provider CRUD to handle API key save/redaction

**Files:**
- Modify: `consensus/app.py:8-11,41-83`

**Step 1: Update imports in app.py**

Change the imports to:

```python
from .models import (
    Discussion, Entity, EntityType, Message, MessageRole, StoryboardEntry,
    resolve_api_key,
)
from .config import save_api_key, remove_api_key, has_api_key
```

**Step 2: Update add_provider**

Replace the current `add_provider` method with:

```python
    def add_provider(self, name: str, base_url: str,
                     api_key_env: str = "",
                     api_key: str = "") -> Optional[dict]:
        """Add a new API provider and return its data.

        If *api_key* is provided, save it to ~/.consensus/.env and store
        only the env var name in the database.
        """
        if api_key and api_key_env:
            save_api_key(api_key_env, api_key)
        pid = self.db.add_provider(name, base_url, api_key_env)
        return self._provider_for_frontend(self.db.get_provider(pid))
```

**Step 3: Update update_provider**

Replace the current `update_provider` method with:

```python
    def update_provider(self, provider_id: int,
                        api_key: str = "", **kwargs: object) -> bool:
        """Update an existing provider's fields.

        If *api_key* is provided (non-empty string), save it.
        If *api_key* is the sentinel "__REMOVE__", delete the stored key.
        """
        provider = self.db.get_provider(provider_id)
        if not provider:
            return False
        env_var = kwargs.get("api_key_env") or provider["api_key_env"]
        if api_key == "__REMOVE__" and env_var:
            remove_api_key(env_var)
        elif api_key and env_var:
            save_api_key(env_var, api_key)
        self.db.update_provider(provider_id, **kwargs)
        return True
```

**Step 4: Add _provider_for_frontend helper and update get_providers**

Add a helper method and update `get_providers` and `get_state`:

```python
    @staticmethod
    def _provider_for_frontend(p: Optional[dict]) -> Optional[dict]:
        """Redact secrets before sending provider data to the frontend."""
        if not p:
            return None
        p = dict(p)
        p["has_key"] = has_api_key(p.get("api_key_env") or "")
        p.pop("api_key_env", None)
        return p

    def get_providers(self) -> list[dict]:
        """Return all configured providers (keys redacted)."""
        return [self._provider_for_frontend(p)
                for p in self.db.get_providers()]
```

Update `get_state` to use the new `get_providers`:

```python
    def get_state(self) -> dict:
        """Return the complete application state for the frontend."""
        state = self.discussion.to_dict()
        state["providers"] = self.get_providers()
        state["saved_entities"] = self.db.get_entities()
        state["prompts"] = self.db.get_prompts()
        state["discussions_history"] = self.db.get_discussions()
        return state
```

Keep `fetch_models` using the raw DB provider (needs the actual key):

```python
    async def fetch_models(self, provider_id: int) -> list[str]:
        """Fetch available models from a provider's API."""
        provider = self.db.get_provider(provider_id)
        if not provider:
            return []
        api_key = resolve_api_key(provider["api_key_env"] or "")
        async with AIClient(provider["base_url"], api_key) as client:
            return await client.list_models()
```

**Step 5: Verify imports**

Run: `python -c "from consensus.app import ConsensusApp; print('OK')"`
Expected: `OK`

**Step 6: Commit**

```
git add consensus/app.py
git commit -m "Handle API key save/remove in provider CRUD, redact keys from frontend"
```

---

### Task 5: Update desktop.py and server.py provider routes

**Files:**
- Modify: `consensus/desktop.py:62-79`
- Modify: `consensus/server.py:45-57`

**Step 1: Update DesktopBridge.add_provider**

```python
    def add_provider(self, name: str, base_url: str,
                     api_key_env: str = "",
                     api_key: str = "") -> Optional[dict]:
        """Add a new API provider."""
        return self.app.add_provider(name, base_url, api_key_env, api_key)
```

**Step 2: Update DesktopBridge.update_provider**

```python
    def update_provider(self, provider_id: int, name: str = "",
                        base_url: str = "",
                        api_key_env: str = "",
                        api_key: str = "") -> bool:
        """Update an existing provider."""
        kwargs: dict[str, str] = {}
        if name:
            kwargs["name"] = name
        if base_url:
            kwargs["base_url"] = base_url
        if api_key_env is not None:
            kwargs["api_key_env"] = api_key_env
        return self.app.update_provider(provider_id, api_key=api_key, **kwargs)
```

**Step 3: Update server.py handler lambdas**

In `consensus/server.py`, update the `add_provider` and `update_provider` lambdas:

```python
            "add_provider": lambda: app.add_provider(
                data["name"], data["base_url"],
                data.get("api_key_env", ""),
                data.get("api_key", "")),
            "update_provider": lambda: app.update_provider(
                data["provider_id"],
                api_key=data.get("api_key", ""),
                **{k: v for k, v in data.items()
                   if k not in ("provider_id", "api_key")}),
```

**Step 4: Verify**

Run: `python -c "from consensus.desktop import DesktopBridge; from consensus.server import launch_web; print('OK')"`
Expected: `OK`

**Step 5: Commit**

```
git add consensus/desktop.py consensus/server.py
git commit -m "Pass api_key through desktop bridge and web server routes"
```

---

### Task 6: Migrate existing literal keys from DB to .env

**Files:**
- Modify: `consensus/database.py` — `_migrate_providers` method

**Step 1: Update _migrate_providers in database.py**

Replace the existing `_migrate_providers` method with a version that detects literal keys and moves them to `.env`. Import `save_api_key` from config at the top of the method (lazy import to avoid circular deps).

```python
    def _migrate_providers(self) -> None:
        """Apply provider data fixes for existing databases."""
        from .config import save_api_key

        with self._lock:
            # Fix DeepSeek base_url (was /v1, which breaks /models endpoint)
            self.conn.execute(
                "UPDATE providers SET base_url = ? WHERE base_url = ?",
                ("https://api.deepseek.com", "https://api.deepseek.com/v1"),
            )
            # Add Mistral if not already present
            has_mistral = self.conn.execute(
                "SELECT COUNT(*) FROM providers WHERE base_url LIKE '%api.mistral.ai%'"
            ).fetchone()[0]
            if not has_mistral:
                self.conn.execute(
                    "INSERT INTO providers (name, base_url, api_key_env, "
                    "created_at) VALUES (?,?,?,?)",
                    ("Mistral", "https://api.mistral.ai/v1",
                     "MISTRAL_API_KEY", time.time()),
                )

            # Migrate literal API keys out of api_key_env into ~/.consensus/.env
            rows = self.conn.execute(
                "SELECT id, name, api_key_env FROM providers WHERE api_key_env != ''"
            ).fetchall()
            for row in rows:
                value = row[2]  # api_key_env
                # Heuristic: env var names are UPPER_SNAKE_CASE and short.
                # Literal keys contain lowercase, dashes, dots, or are long.
                is_literal = (
                    any(c in value for c in "-.") or
                    value != value.upper() or
                    len(value) > 40
                )
                if is_literal:
                    # Derive env var name from provider name
                    env_var = row[1].upper().replace(" ", "_").replace("(", "").replace(")", "") + "_API_KEY"
                    # Deduplicate: e.g. "ANTHROPIC_API_KEY" not "ANTHROPIC_API_KEY_API_KEY"
                    if env_var.endswith("_API_KEY_API_KEY"):
                        env_var = env_var[:-8]
                    save_api_key(env_var, value)
                    self.conn.execute(
                        "UPDATE providers SET api_key_env = ? WHERE id = ?",
                        (env_var, row[0]),
                    )

            self.conn.commit()
```

**Step 2: Verify migration logic**

Run:
```bash
python -c "
from consensus.database import Database
from consensus.config import get_env_path, has_api_key
import tempfile, os

# Simulate old DB with literal key
with tempfile.TemporaryDirectory() as d:
    import sqlite3, time
    path = os.path.join(d, 'test.db')
    conn = sqlite3.connect(path)
    conn.execute('CREATE TABLE providers (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, base_url TEXT NOT NULL, api_key_env TEXT NOT NULL DEFAULT \"\", created_at REAL NOT NULL)')
    conn.execute('CREATE TABLE schema_version (version INTEGER NOT NULL)')
    now = time.time()
    conn.execute('INSERT INTO providers (name, base_url, api_key_env, created_at) VALUES (?,?,?,?)',
                 ('Anthropic', 'https://api.anthropic.com/v1', 'sk-ant-fake-key-here', now))
    conn.commit()
    conn.close()

    db = Database(path)
    providers = db.get_providers()
    for p in providers:
        print(f'{p[\"name\"]:20s} | api_key_env={p[\"api_key_env\"]}')

    # Check .env was written
    assert has_api_key('ANTHROPIC_API_KEY'), 'Key should be in env now'
    print('Migration OK')
    db.close()
"
```
Expected: `Anthropic` shows `api_key_env=ANTHROPIC_API_KEY`, and `Migration OK`

**Step 3: Commit**

```
git add consensus/database.py
git commit -m "Migrate literal API keys from DB to ~/.consensus/.env"
```

---

### Task 7: Update frontend — provider list, dialog, and API adapters

**Files:**
- Modify: `consensus/static/index.html:170-194`
- Modify: `consensus/static/app.js` — DesktopAPI, WebAPI, renderProviders, openProviderDialog, confirmProvider

**Step 1: Update index.html provider dialog**

Replace the provider dialog form group for the key (lines 182-188) with:

```html
            <div class="form-group">
                <label for="prov-key-env">API Key Variable Name</label>
                <input id="prov-key-env" type="text" placeholder="e.g. ANTHROPIC_API_KEY">
                <span class="text-muted" style="font-size:0.75rem">
                    Environment variable name for this provider's key
                </span>
            </div>
            <div class="form-group">
                <label for="prov-api-key">API Key</label>
                <input id="prov-api-key" type="password" placeholder="Enter API key" autocomplete="off">
                <span id="prov-key-hint" class="text-muted" style="font-size:0.75rem">
                    Leave blank to keep current key
                </span>
            </div>
```

**Step 2: Update DesktopAPI adapter**

In `app.js`, update DesktopAPI:

```javascript
    async addProvider(n, u, ke, k) { return await window.pywebview.api.add_provider(n, u, ke || '', k || ''); }
    async updateProvider(id, n, u, ke, k) { return await window.pywebview.api.update_provider(id, n, u, ke, k || ''); }
```

**Step 3: Update WebAPI adapter**

In `app.js`, update WebAPI:

```javascript
    async addProvider(n, u, ke, k) { return await this._post('add_provider', { name: n, base_url: u, api_key_env: ke || '', api_key: k || '' }); }
    async updateProvider(id, n, u, ke, k) { return await this._post('update_provider', { provider_id: id, name: n, base_url: u, api_key_env: ke, api_key: k || '' }); }
```

**Step 4: Update renderProviders**

Replace the key display line in `renderProviders()`:

```javascript
                <div class="settings-detail">API Key: ${p.has_key ? '<span style="color:var(--color-success)">Configured</span>' : '<em>Not set</em>'}</div>
```

**Step 5: Update openProviderDialog**

```javascript
function openProviderDialog(provider) {
    $('#provider-dialog-title').textContent = provider ? 'Edit Provider' : 'Add Provider';
    $('#prov-name').value = provider?.name || '';
    $('#prov-url').value = provider?.base_url || '';
    $('#prov-key-env').value = provider?.api_key_env || '';
    $('#prov-api-key').value = '';
    $('#prov-edit-id').value = provider?.id || '';
    // Show appropriate hint
    const hint = $('#prov-key-hint');
    if (provider?.has_key) {
        hint.textContent = 'Leave blank to keep current key, or enter new key to replace';
    } else {
        hint.textContent = 'Enter the API key for this provider';
    }
    show('#provider-dialog');
    $('#prov-name').focus();
}
```

**Step 6: Update confirmProvider**

```javascript
async function confirmProvider() {
    const name = $('#prov-name').value.trim();
    const url = $('#prov-url').value.trim();
    if (!name || !url) return showToast('Name and URL are required');
    const keyEnv = $('#prov-key-env').value.trim();
    const apiKey = $('#prov-api-key').value.trim();
    const editId = $('#prov-edit-id').value;

    if (editId) {
        await api.updateProvider(editId, name, url, keyEnv, apiKey);
    } else {
        await api.addProvider(name, url, keyEnv, apiKey);
    }
    const s = await api.getState();
    onStateUpdate(s);
    hide('#provider-dialog');
    renderProviders();
}
```

**Step 7: Verify visually**

Run: `python -m consensus --web --debug` and open in browser.
- Check Providers tab: each provider should show "Configured" or "Not set"
- Open edit dialog: API Key field should be blank (never shows actual key)
- Enter a new key, save, verify it shows "Configured"

**Step 8: Commit**

```
git add consensus/static/index.html consensus/static/app.js
git commit -m "Update frontend: mask API keys, show configured status, split key input"
```

---

### Task 8: Final integration test and cleanup

**Files:**
- No new files

**Step 1: Run full startup test**

```bash
python -c "
from consensus.config import load_env
load_env()
from consensus.app import ConsensusApp
app = ConsensusApp()

# Verify providers are redacted
providers = app.get_providers()
for p in providers:
    assert 'api_key_env' not in p, f'api_key_env leaked for {p[\"name\"]}'
    assert 'has_key' in p, f'has_key missing for {p[\"name\"]}'
    print(f'{p[\"name\"]:20s} | has_key={p[\"has_key\"]}')

print('All providers redacted correctly')
"
```
Expected: All providers listed with `has_key=True/False`, no `api_key_env` in output.

**Step 2: Verify model fetching still works**

```bash
python -c "
import asyncio
from consensus.config import load_env
load_env()
from consensus.app import ConsensusApp

async def test():
    app = ConsensusApp()
    providers = app.db.get_providers()  # raw, for testing
    for p in providers:
        models = await app.fetch_models(p['id'])
        print(f'{p[\"name\"]:20s} | {len(models)} models')

asyncio.run(test())
"
```
Expected: Anthropic, DeepSeek, Ollama should show model counts (if keys/services are available).

**Step 3: Check .env file exists and has restrictive permissions**

```bash
ls -la ~/.consensus/.env
```
Expected: `-rw-------` permissions.

**Step 4: Final commit (squash or leave as-is)**

All done. The task chain above produced 7 incremental commits.
