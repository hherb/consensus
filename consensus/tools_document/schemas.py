"""JSON schemas for the ``doc_*`` tool parameters."""

# ---------------------------------------------------------------------------
# Tool schemas
# ---------------------------------------------------------------------------

_DOC_ADD_SCHEMA = {
    "type": "object",
    "properties": {
        "url": {
            "type": "string",
            "description": "URL to fetch and add as a document.",
        },
        "text": {
            "type": "string",
            "description": "Inline text content to add as a document.",
        },
        "title": {
            "type": "string",
            "description": "Title for the document (auto-detected if not provided).",
        },
        "filename": {
            "type": "string",
            "description": "Filename for the document (auto-detected if not provided).",
        },
    },
}

_DOC_LIST_SCHEMA = {
    "type": "object",
    "properties": {
        "full_library": {
            "type": "boolean",
            "description": (
                "If true, search all documents in the database (not just this discussion). "
                "Requires 'query' parameter for semantic search."
            ),
            "default": False,
        },
        "query": {
            "type": "string",
            "description": "Search query for full_library mode. Finds documents by semantic similarity.",
        },
    },
}

_DOC_LENGTH_SCHEMA = {
    "type": "object",
    "properties": {
        "document_id": {
            "type": "integer",
            "description": "ID of the document to query.",
        },
    },
    "required": ["document_id"],
}

_DOC_TEXT_SCHEMA = {
    "type": "object",
    "properties": {
        "document_id": {
            "type": "integer",
            "description": "ID of the document.",
        },
        "from_char": {
            "type": "integer",
            "description": "Start character offset (inclusive, 0-based).",
        },
        "to_char": {
            "type": "integer",
            "description": "End character offset (exclusive, -1 for end of document).",
        },
    },
    "required": ["document_id", "from_char", "to_char"],
}

_DOC_SECTIONS_SCHEMA = {
    "type": "object",
    "properties": {
        "document_id": {
            "type": "integer",
            "description": "ID of the document.",
        },
    },
    "required": ["document_id"],
}

_DOC_CHAPTER_SCHEMA = {
    "type": "object",
    "properties": {
        "document_id": {
            "type": "integer",
            "description": "ID of the document.",
        },
        "header": {
            "type": "string",
            "description": "Section header text to retrieve (case-insensitive match).",
        },
    },
    "required": ["document_id", "header"],
}

_DOC_ASK_SCHEMA = {
    "type": "object",
    "properties": {
        "document_id": {
            "type": "integer",
            "description": "ID of the document to query.",
        },
        "question": {
            "type": "string",
            "description": "Question to ask about the document.",
        },
    },
    "required": ["document_id", "question"],
}

_DOC_SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "document_id": {
            "type": "integer",
            "description": "ID of the document.",
        },
        "from_char": {
            "type": "integer",
            "description": "Start character offset (default 0).",
            "default": 0,
        },
        "to_char": {
            "type": "integer",
            "description": "End character offset (default -1 = end of document).",
            "default": -1,
        },
    },
    "required": ["document_id"],
}
