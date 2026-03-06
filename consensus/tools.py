"""Pluggable tool-use framework for AI participants.

Provides an abstract ToolProvider interface with two backends:
- PythonToolProvider: in-process Python callables
- MCPToolProvider: external MCP server connections (future)

The ToolRegistry aggregates tools from multiple providers and handles
access control (private, shared, moderator-only).
"""

import asyncio
import json
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Union

logger = logging.getLogger(__name__)

# Safety limits
TOOL_EXECUTION_TIMEOUT = 30.0  # seconds
MAX_TOOL_ITERATIONS = 5


@dataclass
class ToolDefinition:
    """Schema for a tool in OpenAI function-calling format."""
    name: str
    description: str
    parameters: dict  # JSON Schema object
    provider_name: str = ""

    def to_openai_schema(self) -> dict:
        """Convert to OpenAI tools parameter format."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclass
class ToolResult:
    """Result from executing a tool, returned to the LLM."""
    content: str
    is_error: bool = False
    metadata: dict = field(default_factory=dict)


@dataclass
class ToolCallRecord:
    """Record of a tool call for persistence and display."""
    tool_name: str
    arguments: dict
    result: str
    is_error: bool = False
    latency_ms: int = 0

    def to_dict(self) -> dict:
        return {
            "tool_name": self.tool_name,
            "arguments": self.arguments,
            "result": self.result,
            "is_error": self.is_error,
            "latency_ms": self.latency_ms,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ToolCallRecord":
        return cls(
            tool_name=d["tool_name"],
            arguments=d.get("arguments", {}),
            result=d.get("result", ""),
            is_error=d.get("is_error", False),
            latency_ms=d.get("latency_ms", 0),
        )


@dataclass
class ToolContext:
    """Context passed to tool execution for access control and state namespacing."""
    caller_entity_id: int
    discussion_id: int
    access_mode: str = "private"  # private, shared, moderator_only


class ToolProvider(ABC):
    """Abstract base for tool providers."""

    def __init__(self, name: str) -> None:
        self.name = name

    @abstractmethod
    async def list_tools(self) -> list[ToolDefinition]:
        """Return all tools this provider offers."""

    @abstractmethod
    async def execute(self, tool_name: str, arguments: dict,
                      context: ToolContext) -> ToolResult:
        """Execute a tool and return the result."""

    async def close(self) -> None:
        """Cleanup resources. Override for MCP connections."""
        pass


class PythonToolProvider(ToolProvider):
    """Tool provider that wraps in-process Python callables.

    Tools are registered as (callable, ToolDefinition) pairs.
    Callables can be sync or async functions accepting (arguments, context).
    """

    def __init__(self, name: str = "builtin") -> None:
        super().__init__(name)
        self._tools: dict[str, tuple[Callable, ToolDefinition]] = {}

    def register(self, tool_def: ToolDefinition,
                 handler: Callable[..., Any]) -> None:
        """Register a Python callable as a tool."""
        tool_def.provider_name = self.name
        self._tools[tool_def.name] = (handler, tool_def)

    async def list_tools(self) -> list[ToolDefinition]:
        return [td for _, td in self._tools.values()]

    async def execute(self, tool_name: str, arguments: dict,
                      context: ToolContext) -> ToolResult:
        entry = self._tools.get(tool_name)
        if not entry:
            return ToolResult(
                content=f"Unknown tool: {tool_name}", is_error=True,
            )
        handler, _ = entry
        try:
            if asyncio.iscoroutinefunction(handler):
                result = await handler(arguments, context)
            else:
                result = handler(arguments, context)
            if isinstance(result, ToolResult):
                return result
            return ToolResult(content=str(result))
        except Exception as e:
            logger.exception("Tool %s execution failed", tool_name)
            return ToolResult(content=f"Tool error: {e}", is_error=True)


class ToolRegistry:
    """Aggregates tools from multiple providers with access control.

    Held by ConsensusApp; passed to Moderator for tool-augmented generation.
    """

    def __init__(self, db: Any = None) -> None:
        self._providers: dict[str, ToolProvider] = {}
        self._db = db  # Database instance for reading entity_tools

    def register_provider(self, provider: ToolProvider) -> None:
        """Register a tool provider."""
        self._providers[provider.name] = provider

    async def list_all_tools(self) -> list[ToolDefinition]:
        """Return all tools from all providers."""
        tools: list[ToolDefinition] = []
        for provider in self._providers.values():
            tools.extend(await provider.list_tools())
        return tools

    async def get_tools_for_entity(
        self, entity_id: int, discussion_id: int,
        moderator_id: Optional[int] = None,
    ) -> list[ToolDefinition]:
        """Get tools available to an entity, respecting assignments and access modes.

        Checks entity_tools table for assignments, then applies
        discussion_tool_overrides if any exist.
        """
        if not self._db:
            return []

        all_tools = {t.name: t for t in await self.list_all_tools()}
        if not all_tools:
            return []

        # Get entity's tool assignments from DB
        assignments = self._db.get_entity_tools(entity_id)
        # Get per-discussion overrides
        overrides = self._db.get_discussion_tool_overrides(
            discussion_id, entity_id,
        )
        override_map = {o["tool_name"]: o["enabled"] for o in overrides}

        # Also get shared tools assigned to other entities in this discussion
        shared_tools = self._db.get_shared_tools_for_discussion(discussion_id)

        result: list[ToolDefinition] = []

        # Process direct assignments
        for assignment in assignments:
            tool_name = assignment["tool_name"]
            access_mode = assignment["access_mode"]

            # Check discussion override
            if tool_name in override_map:
                if not override_map[tool_name]:
                    continue  # Disabled for this discussion

            # Check if tool is enabled
            if not assignment.get("enabled", True):
                continue

            # Moderator-only check
            if access_mode == "moderator_only" and entity_id != moderator_id:
                continue

            if tool_name in all_tools:
                result.append(all_tools[tool_name])

        # Add shared tools from other entities
        for shared in shared_tools:
            tool_name = shared["tool_name"]
            if tool_name not in [t.name for t in result] and tool_name in all_tools:
                # Check discussion override for this entity
                if tool_name in override_map and not override_map[tool_name]:
                    continue
                result.append(all_tools[tool_name])

        return result

    async def execute(
        self, tool_name: str, arguments: dict,
        caller_entity_id: int, discussion_id: int,
        moderator_id: Optional[int] = None,
    ) -> ToolResult:
        """Execute a tool with access control checks."""
        # Find the provider that owns this tool
        provider: Optional[ToolProvider] = None
        for p in self._providers.values():
            tool_names = [t.name for t in await p.list_tools()]
            if tool_name in tool_names:
                provider = p
                break

        if not provider:
            return ToolResult(
                content=f"Unknown tool: {tool_name}", is_error=True,
            )

        # Access control check
        if self._db:
            assignment = self._db.get_entity_tool(
                caller_entity_id, tool_name,
            )
            if not assignment:
                # Check if it's a shared tool from another entity
                shared = self._db.get_shared_tools_for_discussion(discussion_id)
                shared_names = [s["tool_name"] for s in shared]
                if tool_name not in shared_names:
                    return ToolResult(
                        content=f"Access denied: {tool_name} is not assigned to you",
                        is_error=True,
                    )
            elif assignment["access_mode"] == "moderator_only":
                if caller_entity_id != moderator_id:
                    return ToolResult(
                        content=f"Access denied: {tool_name} is moderator-only",
                        is_error=True,
                    )

        # Determine access mode for context
        access_mode = "private"
        if self._db:
            assignment = self._db.get_entity_tool(caller_entity_id, tool_name)
            if assignment:
                access_mode = assignment["access_mode"]

        context = ToolContext(
            caller_entity_id=caller_entity_id,
            discussion_id=discussion_id,
            access_mode=access_mode,
        )

        # Execute with timeout
        try:
            result = await asyncio.wait_for(
                provider.execute(tool_name, arguments, context),
                timeout=TOOL_EXECUTION_TIMEOUT,
            )
            return result
        except asyncio.TimeoutError:
            return ToolResult(
                content=f"Tool {tool_name} timed out after {TOOL_EXECUTION_TIMEOUT}s",
                is_error=True,
            )
        except Exception as e:
            logger.exception("Tool execution failed: %s", tool_name)
            return ToolResult(content=f"Tool error: {e}", is_error=True)

    async def close(self) -> None:
        """Close all providers."""
        for provider in self._providers.values():
            try:
                await provider.close()
            except Exception:
                logger.debug("Error closing provider %s", provider.name,
                             exc_info=True)
