"""ConsensusApp mixin: Provider, entity-profile and prompt management.

Moved verbatim from ``consensus/app.py`` (issue #61). The host class
is ``ConsensusApp``, which owns all state.
"""

from typing import Optional

from .. import app_entities, app_providers
from .request_keys import _request_api_keys_var


class CatalogMixin:
    """Provider, entity-profile and prompt management.

    Expects the host class to provide: db, resolve_provider_api_key(),
    _notify().
    """

    # ------------------------------------------------------------------
    # Provider management
    # ------------------------------------------------------------------

    def _provider_for_frontend(self, p: Optional[dict]) -> Optional[dict]:
        """Redact secrets before sending provider data to the frontend."""
        return app_providers.provider_for_frontend(p, _request_api_keys_var.get({}))

    def add_provider(self, name: str, base_url: str,
                     api_key_env: str = "",
                     api_key: str = "") -> Optional[dict]:
        """Add a new API provider and return its data."""
        return app_providers.add_provider(
            self.db, name, base_url, api_key_env, api_key,
            _request_api_keys_var.get({}),
        )

    def update_provider(self, provider_id: int,
                        api_key: str = "", **kwargs: object) -> bool:
        """Update an existing provider's fields."""
        return app_providers.update_provider(
            self.db, provider_id, api_key, **kwargs,
        )

    def delete_provider(self, provider_id: int) -> bool:
        """Delete a provider by ID."""
        return app_providers.delete_provider(self.db, provider_id)

    def get_providers(self) -> list[dict]:
        """Return all configured providers (keys redacted)."""
        return app_providers.get_providers(self.db, _request_api_keys_var.get({}))

    async def fetch_models(self, provider_id: int) -> list[str]:
        """Fetch available models from a provider's API."""
        return await app_providers.fetch_models(
            self.db, provider_id, self.resolve_provider_api_key,
        )

    # ------------------------------------------------------------------
    # Entity profile management (persistent)
    # ------------------------------------------------------------------

    def save_entity(self, name: str, entity_type: str,
                    avatar_color: str = "#3b82f6",
                    provider_id: int = 0, model: str = "",
                    temperature: float = 0.7, max_tokens: int = 1024,
                    system_prompt: str = "",
                    entity_id: int = 0) -> Optional[dict]:
        """Create or update a persistent entity profile."""
        return app_entities.save_entity(
            self.db, name, entity_type, avatar_color, provider_id,
            model, temperature, max_tokens, system_prompt, entity_id,
        )

    def delete_entity(self, entity_id: int) -> dict:
        """Delete or deactivate an entity profile by ID."""
        return app_entities.delete_entity(self.db, entity_id)

    def reactivate_entity(self, entity_id: int) -> bool:
        """Reactivate a previously deactivated entity profile."""
        return app_entities.reactivate_entity(self.db, entity_id)

    def get_entities(self) -> list[dict]:
        """Return all saved active entity profiles."""
        return app_entities.get_entities(self.db)

    def get_inactive_entities(self) -> list[dict]:
        """Return all inactive (soft-deleted) entity profiles."""
        return app_entities.get_inactive_entities(self.db)

    # ------------------------------------------------------------------
    # Prompt management
    # ------------------------------------------------------------------

    def save_prompt(self, prompt_id: int, name: str, role: str,
                    target: str, task: str, content: str) -> Optional[dict]:
        """Create or update a prompt template."""
        pid = self.db.save_prompt(
            prompt_id or None, name, role, target, task, content,
        )
        return self.db.get_prompt(pid)

    def delete_prompt(self, prompt_id: int) -> bool:
        """Delete a prompt template by ID."""
        self.db.delete_prompt(prompt_id)
        return True

    def get_prompts(self) -> list[dict]:
        """Return all prompt templates."""
        return self.db.get_prompts()
