"""The MCP tool definitions the server advertises, and their registration."""

from __future__ import annotations

from typing import Any


# ---------------------------------------------------------------------------
# Tool definition helpers
# ---------------------------------------------------------------------------

def _tool_def(name: str, description: str, properties: dict,
              required: list[str] | None = None) -> dict:
    """Build an MCP tool definition dict."""
    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return {"name": name, "description": description, "inputSchema": schema}


class ToolRegistrationMixin:
    """Registers every tool's definition with its ``_handle_*`` method."""

    def _register(self, definition: dict, handler):
        self._tools[definition["name"]] = {
            "definition": definition,
            "handler": handler,
        }

    def _register_all_tools(self):
        # Passive — list / read
        self._register(
            _tool_def("list_discussions",
                       "List discussions, optionally filtered by status.",
                       {"status": {"type": "string",
                                   "description": "Filter: setup, active, paused, or concluded."},
                        "limit": {"type": "integer", "description": "Max results (default 20)."}},
                       ),
            self._handle_list_discussions)

        self._register(
            _tool_def("read_discussion",
                       "Read a discussion's full transcript and storyboard.",
                       {"discussion_id": {"type": "integer", "description": "Discussion ID."},
                        "include_messages": {"type": "boolean",
                                             "description": "Include messages (default true)."},
                        "include_storyboard": {"type": "boolean",
                                               "description": "Include storyboard (default true)."}},
                       ["discussion_id"]),
            self._handle_read_discussion)

        self._register(
            _tool_def("list_entities",
                       "List entity profiles (AI participants, humans, experts).",
                       {"entity_type": {"type": "string",
                                        "description": "Filter: human, ai, or expert."}}),
            self._handle_list_entities)

        self._register(
            _tool_def("list_documents",
                       "List all documents in the library.",
                       {}),
            self._handle_list_documents)

        self._register(
            _tool_def("read_document",
                       "Read a document's full text or a specific section.",
                       {"document_id": {"type": "integer", "description": "Document ID."},
                        "section_header": {"type": "string",
                                           "description": "Optional section header to read."}},
                       ["document_id"]),
            self._handle_read_document)

        # Passive — semantic search
        self._register(
            _tool_def("search_discussions",
                       "Semantically search across all discussion messages.",
                       {"query": {"type": "string", "description": "Search query."},
                        "limit": {"type": "integer", "description": "Max results (default 5)."},
                        "topic_filter": {"type": "string",
                                         "description": "Filter to discussions whose topic contains this."}},
                       ["query"]),
            self._handle_search_discussions)

        self._register(
            _tool_def("search_memories",
                       "Search an entity's long-term memories semantically. "
                       "Use entity_id=0 for the coding agent's own memories.",
                       {"entity_id": {"type": "integer",
                                      "description": "Entity ID (0 = own agent entity)."},
                        "query": {"type": "string", "description": "Search query."},
                        "limit": {"type": "integer", "description": "Max results (default 5)."}},
                       ["query"]),
            self._handle_search_memories)

        self._register(
            _tool_def("search_documents",
                       "Semantically search across all ingested documents.",
                       {"query": {"type": "string", "description": "Search query."},
                        "limit": {"type": "integer", "description": "Max results (default 5)."}},
                       ["query"]),
            self._handle_search_documents)

        self._register(
            _tool_def("query_knowledge_graph",
                       "Query the knowledge graph by semantic search or node neighbors.",
                       {"query": {"type": "string", "description": "Concept or search phrase."},
                        "mode": {"type": "string", "enum": ["search", "neighbors"],
                                 "description": "search = semantic; neighbors = exact node edges."},
                        "limit": {"type": "integer", "description": "Max results (default 5)."}},
                       ["query"]),
            self._handle_query_knowledge_graph)

        # Active — write
        self._register(
            _tool_def("store_memory",
                       "Store a persistent memory for the coding agent (or another entity). "
                       "Use entity_id=0 (default) for the agent's own memory.",
                       {"content": {"type": "string",
                                    "description": "The memory to store."},
                        "entity_id": {"type": "integer",
                                      "description": "Entity ID (0 = own agent, default)."}},
                       ["content"]),
            self._handle_store_memory)

        self._register(
            _tool_def("delete_memory",
                       "Delete one of the agent's own memories by ID. "
                       "Cannot delete other entities' memories.",
                       {"memory_id": {"type": "string",
                                      "description": "Memory ID (from search_memories)."}},
                       ["memory_id"]),
            self._handle_delete_memory)

        self._register(
            _tool_def("assert_knowledge",
                       "Assert a knowledge triple: subject --[relation]--> object.",
                       {"subject": {"type": "string", "description": "Subject concept."},
                        "relation": {"type": "string",
                                     "description": "Relation (supports, contradicts, implies, etc)."},
                        "object": {"type": "string", "description": "Object concept."},
                        "description": {"type": "string",
                                        "description": "Optional evidence or context."}},
                       ["subject", "relation", "object"]),
            self._handle_assert_knowledge)

        self._register(
            _tool_def("run_discussion",
                       "Create and run a full AI discussion on a topic, returning the "
                       "synthesised conclusion. May take several minutes.",
                       {"topic": {"type": "string", "description": "Discussion topic or question."},
                        "entity_ids": {"type": "array", "items": {"type": "integer"},
                                       "description": "Participant entity IDs (AI only). "
                                                       "Omit to auto-select."},
                        "max_rounds": {"type": "integer",
                                       "description": "Max discussion rounds (default 3)."},
                        "moderator_id": {"type": "integer",
                                         "description": "Moderator entity ID (default: first entity)."},
                        "cost_limit": {"type": "number",
                                       "description": "USD cost limit (default 1.00)."}},
                       ["topic"]),
            self._handle_run_discussion)
