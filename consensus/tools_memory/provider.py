"""Factory assembling the institutional memory tool provider."""

from ..tools import PythonToolProvider, ToolContext, ToolDefinition, ToolResult
from .embedding import EmbeddingClient
from .handlers import (
    _discussion_search_handler, _memory_forget_handler, _memory_recall_handler,
    _memory_store_handler,
)
from .handlers_kg import _kg_assert_handler, _kg_query_handler
from .schemas import (
    _DISCUSSION_SEARCH_SCHEMA, _KG_ASSERT_SCHEMA, _KG_QUERY_SCHEMA,
    _MEMORY_FORGET_SCHEMA, _MEMORY_RECALL_SCHEMA, _MEMORY_STORE_SCHEMA,
)

# ---------------------------------------------------------------------------
# Provider factory
# ---------------------------------------------------------------------------

def create_memory_provider(db) -> PythonToolProvider:
    """Create and return the institutional memory tool provider."""
    embed_client = EmbeddingClient(db)
    provider = PythonToolProvider(name="memory")

    def _make_handler(fn):
        async def handler(arguments: dict, context: ToolContext) -> ToolResult:
            return await fn(arguments, context, db, embed_client)
        return handler

    provider.register(
        ToolDefinition(
            name="memory_store",
            description=(
                "Store an observation, position, or reflection to your long-term memory. "
                "Use this to remember insights, stances, or evidence across discussions."
            ),
            parameters=_MEMORY_STORE_SCHEMA,
        ),
        _make_handler(_memory_store_handler),
    )

    provider.register(
        ToolDefinition(
            name="memory_recall",
            description=(
                "Search your personal long-term memory for past observations or positions. "
                "Returns the most semantically relevant memories."
            ),
            parameters=_MEMORY_RECALL_SCHEMA,
        ),
        _make_handler(_memory_recall_handler),
    )

    provider.register(
        ToolDefinition(
            name="memory_forget",
            description=(
                "Delete a specific memory from your long-term memory by its ID. "
                "Use when a stored memory is no longer accurate or relevant."
            ),
            parameters=_MEMORY_FORGET_SCHEMA,
        ),
        _make_handler(_memory_forget_handler),
    )

    provider.register(
        ToolDefinition(
            name="discussion_search",
            description=(
                "Semantically search across all past discussion messages to find relevant "
                "arguments, evidence, or prior reasoning from earlier discussions."
            ),
            parameters=_DISCUSSION_SEARCH_SCHEMA,
        ),
        _make_handler(_discussion_search_handler),
    )

    provider.register(
        ToolDefinition(
            name="kg_assert",
            description=(
                "Assert a knowledge triple: (subject) --[relation]--> (object). "
                "Builds a persistent knowledge graph of concepts and their relationships. "
                "Example: subject='free will', relation='contradicts', object='determinism'"
            ),
            parameters=_KG_ASSERT_SCHEMA,
        ),
        _make_handler(_kg_assert_handler),
    )

    provider.register(
        ToolDefinition(
            name="kg_query",
            description=(
                "Query the knowledge graph. "
                "mode='search': find nodes semantically similar to your query. "
                "mode='neighbors': get all connections of a specific node."
            ),
            parameters=_KG_QUERY_SCHEMA,
        ),
        _make_handler(_kg_query_handler),
    )

    return provider
