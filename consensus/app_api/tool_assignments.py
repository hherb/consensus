"""ConsensusApp mixin: Tool listing and per-entity / per-discussion tool assignment.

Moved verbatim from ``consensus/app.py`` (issue #61). The host class
is ``ConsensusApp``, which owns all state.
"""


class ToolAssignmentsMixin:
    """Tool listing and per-entity / per-discussion tool assignment.

    Expects the host class to provide: db, tool_registry.
    """

    # ------------------------------------------------------------------
    # Tool management
    # ------------------------------------------------------------------

    async def list_available_tools(self) -> list[dict]:
        """Return all tools from all registered providers."""
        tools = await self.tool_registry.list_all_tools()
        return [
            {"name": t.name, "description": t.description,
             "parameters": t.parameters, "provider": t.provider_name}
            for t in tools
        ]

    def get_entity_tools(self, entity_id: int) -> list[dict]:
        """Return tool assignments for an entity."""
        return self.db.get_entity_tools(entity_id)

    def assign_tool_to_entity(self, entity_id: int, tool_name: str,
                               access_mode: str = "private") -> bool:
        """Assign a tool to an entity with the specified access mode."""
        self.db.add_entity_tool(entity_id, tool_name, access_mode)
        return True

    def remove_entity_tool(self, entity_id: int, tool_name: str) -> bool:
        """Remove a tool assignment from an entity."""
        self.db.remove_entity_tool(entity_id, tool_name)
        return True

    def set_discussion_tool_override(self, discussion_id: int, entity_id: int,
                                      tool_name: str, enabled: bool) -> bool:
        """Set a per-discussion tool override."""
        self.db.set_discussion_tool_override(
            discussion_id, entity_id, tool_name, enabled,
        )
        return True
