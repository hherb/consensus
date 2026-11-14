"""Bridge methods for the setup catalogue: providers, entity
profiles, prompts, tool assignments, MCP servers, experts and
the memory configuration."""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


class CatalogBridgeMixin:
    """Providers, entities, prompts, tools, MCP servers, experts
    and memory configuration."""

    # -- Providers --
    def add_provider(self, name: str, base_url: str,
                     api_key_env: str = "",
                     api_key: str = "") -> Optional[dict]:
        """Add a new API provider."""
        return self.app.add_provider(name, base_url, api_key_env, api_key)

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

    def delete_provider(self, provider_id: int) -> bool:
        """Delete a provider by ID."""
        return self.app.delete_provider(provider_id)

    def fetch_models(self, provider_id: int) -> list:
        """Fetch available models from a provider's API."""
        return self._run_async(self.app.fetch_models(provider_id))

    # -- Entity profiles --
    def save_entity(self, name: str, entity_type: str,
                    avatar_color: str = "#3b82f6",
                    provider_id: int = 0, model: str = "",
                    temperature: float = 0.7, max_tokens: int = 1024,
                    system_prompt: str = "", entity_id: int = 0) -> Optional[dict]:
        """Create or update a persistent entity profile."""
        return self.app.save_entity(
            name, entity_type, avatar_color, provider_id,
            model, temperature, max_tokens, system_prompt, entity_id,
        )

    def delete_entity(self, entity_id: int) -> dict:
        """Delete or deactivate an entity profile."""
        return self.app.delete_entity(entity_id)

    def reactivate_entity(self, entity_id: int) -> bool:
        """Reactivate a previously deactivated entity profile."""
        return self.app.reactivate_entity(entity_id)

    def get_inactive_entities(self) -> list[dict]:
        """Return all inactive (soft-deleted) entity profiles."""
        return self.app.get_inactive_entities()

    # -- Prompts --
    def save_prompt(self, prompt_id: int, name: str, role: str,
                    target: str, task: str, content: str) -> Optional[dict]:
        """Create or update a prompt template."""
        return self.app.save_prompt(
            prompt_id, name, role, target, task, content,
        )

    def delete_prompt(self, prompt_id: int) -> bool:
        """Delete a prompt template."""
        return self.app.delete_prompt(prompt_id)

    # -- Tools --
    def list_tools(self) -> list:
        """List all available tools from all providers."""
        return self._run_async(self.app.list_available_tools())

    def get_entity_tools(self, entity_id: int) -> list:
        """Get tool assignments for an entity."""
        return self.app.get_entity_tools(entity_id)

    def assign_tool(self, entity_id: int, tool_name: str,
                    access_mode: str = "private") -> bool:
        """Assign a tool to an entity."""
        return self.app.assign_tool_to_entity(entity_id, tool_name, access_mode)

    def remove_tool(self, entity_id: int, tool_name: str) -> bool:
        """Remove a tool assignment from an entity."""
        return self.app.remove_entity_tool(entity_id, tool_name)

    def set_tool_override(self, discussion_id: int, entity_id: int,
                          tool_name: str, enabled: bool) -> bool:
        """Set a per-discussion tool override."""
        return self.app.set_discussion_tool_override(
            discussion_id, entity_id, tool_name, enabled,
        )

    # -- MCP servers --
    def get_mcp_servers(self) -> list:
        """List all configured MCP servers."""
        return self.app.get_mcp_servers()

    def add_mcp_server(self, name, description, command, args=None, env=None,
                       transport="stdio", url="", headers=None) -> dict:
        """Add a new MCP server configuration."""
        return self.app.add_mcp_server(name, description, command, args, env,
                                       transport=transport, url=url, headers=headers)

    def update_mcp_server(self, server_id, **kwargs) -> bool:
        """Update an existing MCP server configuration."""
        return self.app.update_mcp_server(server_id, **kwargs)

    def delete_mcp_server(self, server_id) -> bool:
        """Delete an MCP server configuration."""
        return self._run_async(self.app.delete_mcp_server(server_id))

    def test_mcp_connection(self, server_id) -> dict:
        """Test connectivity to an MCP server."""
        return self._run_async(self.app.test_mcp_connection(server_id))

    # -- Experts --
    def save_expert_definition(self, entity_id, mcp_server_id, tool_name,
                               description="", default_arguments=None,
                               query_param_name="query", timeout_seconds=300) -> dict:
        """Save an expert definition linking an entity to an MCP tool."""
        return self.app.save_expert_definition(entity_id, mcp_server_id, tool_name,
                                                description, default_arguments,
                                                query_param_name, timeout_seconds)

    def get_expert_definitions(self) -> list:
        """List all expert definitions."""
        return self.app.get_expert_definitions()

    def consult_expert(self, expert_name, query) -> dict:
        """Consult an expert by name with a query."""
        return self._run_async(self.app.consult_expert(expert_name, query))

    # -- Memory config --
    def get_memory_config(self) -> dict:
        """Return memory configuration."""
        if not self.app.memory_available:
            return {"error": "Memory tools failed to load; see the application log for the cause"}
        return self.app.db.get_memory_config()

    def save_memory_config(self, data: dict) -> dict:
        """Update memory configuration keys."""
        if not self.app.memory_available:
            return {"error": "Memory tools failed to load; see the application log for the cause"}
        allowed_keys = {"embedding_backend", "embedding_model", "embedding_endpoint"}
        for key, value in data.items():
            if key in allowed_keys:
                self.app.db.set_memory_config(key, str(value))
        return {"ok": True}

    def test_memory_connection(self) -> dict:
        """Test the embedding connection."""
        if not self.app.memory_available:
            return {"ok": False, "message": "Memory tools failed to load; see the application log for the cause"}
        try:
            from ..tools_memory import EmbeddingClient
            client = EmbeddingClient(self.app.db)
            ok, message = self._run_async(client.test_connection())
            return {"ok": ok, "message": message}
        except Exception as e:
            logger.exception("Memory connection test failed")
            return {"ok": False, "message": str(e)}
