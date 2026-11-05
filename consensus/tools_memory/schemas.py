"""JSON schemas for the institutional memory tools."""

from .constants import SEARCH_DEFAULT_LIMIT

# ---------------------------------------------------------------------------
# Tool schemas
# ---------------------------------------------------------------------------

_MEMORY_STORE_SCHEMA = {
    "type": "object",
    "properties": {
        "content": {
            "type": "string",
            "description": "The observation, position, or reflection to remember.",
        },
    },
    "required": ["content"],
}

_MEMORY_RECALL_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "What to search for in your personal memory.",
        },
        "limit": {
            "type": "integer",
            "description": f"Maximum results to return (default {SEARCH_DEFAULT_LIMIT}).",
            "default": SEARCH_DEFAULT_LIMIT,
        },
    },
    "required": ["query"],
}

_MEMORY_FORGET_SCHEMA = {
    "type": "object",
    "properties": {
        "memory_id": {
            "type": "string",
            "description": "The ID of the memory to delete (returned by memory_recall).",
        },
    },
    "required": ["memory_id"],
}

_DISCUSSION_SEARCH_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "Semantic search query over past discussion messages.",
        },
        "limit": {
            "type": "integer",
            "description": f"Maximum results to return (default {SEARCH_DEFAULT_LIMIT}).",
            "default": SEARCH_DEFAULT_LIMIT,
        },
        "topic_filter": {
            "type": "string",
            "description": "Optional: filter to discussions whose topic contains this string.",
        },
    },
    "required": ["query"],
}

_KG_ASSERT_SCHEMA = {
    "type": "object",
    "properties": {
        "subject": {
            "type": "string",
            "description": "The subject concept or entity label.",
        },
        "relation": {
            "type": "string",
            "description": (
                "The relationship type, e.g. 'supports', 'contradicts', 'implies', "
                "'is_a', 'relates_to', 'causes', 'enables'."
            ),
        },
        "object": {
            "type": "string",
            "description": "The object concept or entity label.",
        },
        "description": {
            "type": "string",
            "description": "Optional description or evidence for this assertion.",
        },
    },
    "required": ["subject", "relation", "object"],
}

KG_QUERY_MODES = ("search", "neighbors")

_KG_QUERY_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "Concept label to look up, or a search phrase for semantic mode.",
        },
        "mode": {
            "type": "string",
            "enum": list(KG_QUERY_MODES),
            "description": (
                "'search': find nodes semantically similar to query; "
                "'neighbors': return edges connected to the matched node."
            ),
            "default": "search",
        },
    },
    "required": ["query"],
}

