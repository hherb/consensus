"""Database mixin for API provider CRUD and seeding."""

import time
from typing import Optional

from ..models import DEFAULT_BASE_URL


class ProvidersMixin:
    """Mixin providing API provider database operations.

    Expects host class to provide:
        conn: sqlite3.Connection
        _lock: threading.Lock
        _execute_write(sql, params) -> sqlite3.Cursor
        _update_row(table, row_id, allowed, **kwargs) -> None
    """

    def _seed_default_providers(self) -> None:
        """Insert default providers only if none exist yet."""
        count = self.conn.execute("SELECT COUNT(*) FROM providers").fetchone()[0]
        if count > 0:
            return

        now = time.time()
        defaults = [
            {
                "name": "Ollama (Local)",
                "base_url": DEFAULT_BASE_URL,
                "api_key_env": "",
            },
            {
                "name": "Anthropic",
                "base_url": "https://api.anthropic.com/v1",
                "api_key_env": "ANTHROPIC_API_KEY",
            },
            {
                "name": "DeepSeek",
                "base_url": "https://api.deepseek.com",
                "api_key_env": "DEEPSEEK_API_KEY",
            },
            {
                "name": "Google Gemini",
                "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
                "api_key_env": "GEMINI_API_KEY",
            },
            {
                "name": "Groq",
                "base_url": "https://api.groq.com/openai/v1",
                "api_key_env": "GROQ_API_KEY",
            },
            {
                "name": "Kimi (Moonshot)",
                "base_url": "https://api.moonshot.ai/v1",
                "api_key_env": "MOONSHOT_API_KEY",
            },
            {
                "name": "Mistral",
                "base_url": "https://api.mistral.ai/v1",
                "api_key_env": "MISTRAL_API_KEY",
            },
            {
                "name": "OpenAI",
                "base_url": "https://api.openai.com/v1",
                "api_key_env": "OPENAI_API_KEY",
            },
            {
                "name": "OpenRouter",
                "base_url": "https://openrouter.ai/api/v1",
                "api_key_env": "OPENROUTER_API_KEY",
            },
            {
                "name": "xAI (Grok)",
                "base_url": "https://api.x.ai/v1",
                "api_key_env": "XAI_API_KEY",
            },
            {
                "name": "Zhipu AI (GLM)",
                "base_url": "https://open.bigmodel.cn/api/paas/v4",
                "api_key_env": "ZHIPU_API_KEY",
            },
        ]

        with self._lock:
            for d in defaults:
                self.conn.execute(
                    "INSERT INTO providers (name, base_url, api_key_env, "
                    "created_at) VALUES (?,?,?,?)",
                    (d["name"], d["base_url"], d["api_key_env"], now),
                )
            self.conn.commit()

    def _migrate_providers(self) -> None:
        """Apply provider data fixes for existing databases."""
        from ..config import save_api_key

        # Collect literal key migrations under the lock, then perform
        # file I/O (save_api_key) outside to avoid holding the lock
        # during potentially blocking filesystem operations.
        keys_to_migrate: list[tuple[int, str, str]] = []  # (id, env_var, value)

        with self._lock:
            # Fix DeepSeek base_url (was /v1, which breaks /models endpoint)
            self.conn.execute(
                "UPDATE providers SET base_url = ? WHERE base_url = ?",
                ("https://api.deepseek.com", "https://api.deepseek.com/v1"),
            )
            # Fix Moonshot base_url: .cn endpoint requires China-issued keys;
            # global .ai endpoint works with all keys.
            self.conn.execute(
                "UPDATE providers SET base_url = ? WHERE base_url = ?",
                ("https://api.moonshot.ai/v1", "https://api.moonshot.cn/v1"),
            )
            # Add missing providers to existing databases
            new_providers = [
                ("Mistral", "api.mistral.ai", "https://api.mistral.ai/v1", "MISTRAL_API_KEY"),
                ("Google Gemini", "generativelanguage.googleapis.com", "https://generativelanguage.googleapis.com/v1beta/openai", "GEMINI_API_KEY"),
                ("Groq", "api.groq.com", "https://api.groq.com/openai/v1", "GROQ_API_KEY"),
                ("Kimi (Moonshot)", "api.moonshot.ai", "https://api.moonshot.ai/v1", "MOONSHOT_API_KEY"),
                ("OpenRouter", "openrouter.ai", "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
                ("xAI (Grok)", "api.x.ai", "https://api.x.ai/v1", "XAI_API_KEY"),
                ("Zhipu AI (GLM)", "open.bigmodel.cn", "https://open.bigmodel.cn/api/paas/v4", "ZHIPU_API_KEY"),
            ]
            for name, domain, base_url, api_key_env in new_providers:
                exists = self.conn.execute(
                    "SELECT COUNT(*) FROM providers WHERE base_url LIKE ?",
                    (f"%{domain}%",),
                ).fetchone()[0]
                if not exists:
                    self.conn.execute(
                        "INSERT INTO providers (name, base_url, api_key_env, "
                        "created_at) VALUES (?,?,?,?)",
                        (name, base_url, api_key_env, time.time()),
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
                    env_var = (row[1].upper()
                               .replace(" ", "_")
                               .replace("(", "")
                               .replace(")", "") + "_API_KEY")
                    if env_var.endswith("_API_KEY_API_KEY"):
                        env_var = env_var[:-8]
                    keys_to_migrate.append((row[0], env_var, value))
                    self.conn.execute(
                        "UPDATE providers SET api_key_env = ? WHERE id = ?",
                        (env_var, row[0]),
                    )

            self.conn.commit()

        # Perform file I/O outside the lock
        for _, env_var, value in keys_to_migrate:
            save_api_key(env_var, value)

    def add_provider(self, name: str, base_url: str,
                     api_key_env: str = "") -> int:
        """Add a new API provider. Returns the new provider ID."""
        cur = self._execute_write(
            "INSERT INTO providers (name,base_url,api_key_env,created_at) "
            "VALUES (?,?,?,?)",
            (name, base_url, api_key_env, time.time()),
        )
        return cur.lastrowid

    def get_providers(self) -> list[dict]:
        """Return all providers ordered by name."""
        return [dict(r) for r in
                self.conn.execute("SELECT * FROM providers ORDER BY name")
                .fetchall()]

    def get_provider(self, provider_id: int) -> Optional[dict]:
        """Retrieve a single provider by ID."""
        row = self.conn.execute(
            "SELECT * FROM providers WHERE id=?", (provider_id,)
        ).fetchone()
        return dict(row) if row else None

    def update_provider(self, provider_id: int, **kwargs: object) -> None:
        """Update a provider's mutable fields."""
        self._update_row(
            "providers", provider_id,
            allowed={"name", "base_url", "api_key_env"},
            **kwargs,
        )

    def delete_provider(self, provider_id: int) -> None:
        """Delete a provider by ID."""
        self._execute_write(
            "DELETE FROM providers WHERE id=?", (provider_id,))
