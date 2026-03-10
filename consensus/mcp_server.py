"""MCP server exposing Consensus data and operations to external AI agents.

Implements the Model Context Protocol (JSON-RPC 2.0 over stdio) so that
tools like Claude Code can search discussion history, query the knowledge
graph, store/recall persistent memories, and even trigger full discussions.

Usage:
    consensus-mcp              # entry point via pyproject.toml
    python -m consensus.mcp_server
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import uuid
from typing import Any, Optional

from .config import get_db_path, load_env
from .database import Database

logger = logging.getLogger(__name__)

JSONRPC_VERSION = "2.0"
MCP_PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "consensus"
SERVER_VERSION = "1.0.0"

AGENT_ENTITY_NAME = "Claude Code Agent"


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


# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------

class ConsensusMCPServer:
    """MCP server exposing Consensus data and operations."""

    def __init__(self, db_path: str = "") -> None:
        self._db = Database(db_path or get_db_path())
        self._embed_client = None  # lazy
        self._agent_entity_id: Optional[int] = None
        self._tools: dict[str, dict] = {}
        self._register_all_tools()

    # -- Lazy helpers -------------------------------------------------------

    def _get_embed_client(self):
        if self._embed_client is None:
            from .tools_memory import EmbeddingClient
            self._embed_client = EmbeddingClient(self._db)
        return self._embed_client

    def _get_agent_entity_id(self) -> int:
        if self._agent_entity_id is not None:
            return self._agent_entity_id
        for e in self._db.get_entities(entity_type="ai"):
            if e["name"] == AGENT_ENTITY_NAME:
                self._agent_entity_id = e["id"]
                return self._agent_entity_id
        self._agent_entity_id = self._db.add_entity(
            name=AGENT_ENTITY_NAME, entity_type="ai")
        return self._agent_entity_id

    def _resolve_entity_id(self, raw: int) -> int:
        """Resolve entity_id=0 to the agent's own entity."""
        return self._get_agent_entity_id() if raw == 0 else int(raw)

    # -- Tool registration --------------------------------------------------

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

    # -----------------------------------------------------------------------
    # Tool handlers — passive list/read
    # -----------------------------------------------------------------------

    async def _handle_list_discussions(self, args: dict) -> str:
        status_filter = args.get("status", "")
        limit = int(args.get("limit", 20))
        rows = self._db.get_discussions()
        if status_filter:
            rows = [r for r in rows if r["status"] == status_filter]
        rows = rows[:limit]
        results = []
        for r in rows:
            results.append({
                "id": r["id"], "topic": r["topic"], "status": r["status"],
                "started_at": r.get("started_at"), "moderator_id": r.get("moderator_id"),
            })
        return json.dumps(results, indent=2)

    async def _handle_read_discussion(self, args: dict) -> str:
        from . import app_discussion_state
        discussion_id = int(args["discussion_id"])
        data = app_discussion_state.get_export_data(self._db, discussion_id)
        if "error" in data:
            return f"Error: {data['error']}"
        include_msgs = args.get("include_messages", True)
        include_sb = args.get("include_storyboard", True)
        if not include_msgs:
            data.pop("messages", None)
        if not include_sb:
            data.pop("storyboard", None)
        return json.dumps(data, indent=2, default=str)

    async def _handle_list_entities(self, args: dict) -> str:
        entity_type = args.get("entity_type", "")
        rows = self._db.get_entities(entity_type=entity_type)
        results = []
        for r in rows:
            results.append({
                "id": r["id"], "name": r["name"],
                "entity_type": r["entity_type"],
                "model": r.get("model", ""),
                "provider_name": r.get("provider_name", ""),
            })
        return json.dumps(results, indent=2)

    async def _handle_list_documents(self, args: dict) -> str:
        rows = self._db.get_all_documents()
        results = []
        for r in rows:
            results.append({
                "id": r["id"], "title": r.get("title", ""),
                "filename": r.get("filename", ""),
                "summary": r.get("summary", ""),
                "char_count": r.get("char_count", 0),
            })
        return json.dumps(results, indent=2)

    async def _handle_read_document(self, args: dict) -> str:
        doc_id = int(args["document_id"])
        section = args.get("section_header", "")
        if section:
            doc = self._db.get_document(doc_id)
            if not doc:
                return "Error: Document not found."
            sections_raw = doc.get("sections_json", "[]")
            try:
                sections = json.loads(sections_raw) if sections_raw else []
            except (json.JSONDecodeError, TypeError):
                sections = []
            target = None
            for s in sections:
                if s.get("header", "").lower() == section.lower():
                    target = s
                    break
            if target:
                chunks = self._db.get_chunks_in_range(
                    doc_id, target["from_char"], target["to_char"])
                text = "\n".join(c["content"] for c in chunks)
                return text or "Section found but no content."
            return f"Section '{section}' not found. Available: {[s['header'] for s in sections]}"
        markdown = self._db.get_document_markdown(doc_id)
        if markdown is None:
            return "Error: Document not found."
        return markdown

    # -----------------------------------------------------------------------
    # Tool handlers — semantic search
    # -----------------------------------------------------------------------

    async def _handle_search_discussions(self, args: dict) -> str:
        from .tools_memory import _rank_by_similarity
        query = args.get("query", "").strip()
        if not query:
            return "Error: No query provided."
        limit = int(args.get("limit", 5))
        topic_filter = args.get("topic_filter")
        try:
            embed = self._get_embed_client()
            query_vec = await embed.embed(query)
        except Exception as e:
            return f"Embedding service unavailable: {e}"
        rows = self._db.get_messages_with_embeddings(topic_filter)
        if not rows:
            return "No indexed discussion messages found."
        top = _rank_by_similarity(query_vec, rows, limit)
        lines = [f"Found {len(top)} relevant passages:\n"]
        for i, row in enumerate(top, 1):
            topic = row.get("topic", "?")
            lines.append(f"{i}. [Discussion: {topic}]\n   {row['content'][:400]}")
        return "\n".join(lines)

    async def _handle_search_memories(self, args: dict) -> str:
        from .tools_memory import _rank_by_similarity
        entity_id = self._resolve_entity_id(int(args.get("entity_id", 0)))
        query = args.get("query", "").strip()
        if not query:
            return "Error: No query provided."
        limit = int(args.get("limit", 5))
        try:
            embed = self._get_embed_client()
            query_vec = await embed.embed(query)
        except Exception as e:
            return f"Embedding service unavailable: {e}"
        rows = self._db.get_entity_memories_with_embeddings(entity_id)
        if not rows:
            return "No memories found for this entity."
        top = _rank_by_similarity(query_vec, rows, limit)
        lines = [f"Recalled {len(top)} memories:\n"]
        for i, row in enumerate(top, 1):
            lines.append(f"{i}. [{row['id']}] {row['content']}")
        return "\n".join(lines)

    async def _handle_search_documents(self, args: dict) -> str:
        from .tools_memory import _rank_by_similarity
        query = args.get("query", "").strip()
        if not query:
            return "Error: No query provided."
        limit = int(args.get("limit", 5))
        try:
            embed = self._get_embed_client()
            query_vec = await embed.embed(query)
        except Exception as e:
            return f"Embedding service unavailable: {e}"
        rows = self._db.get_all_chunks_with_embeddings()
        if not rows:
            return "No indexed documents found."
        top = _rank_by_similarity(query_vec, rows, limit)
        lines = [f"Found {len(top)} relevant chunks:\n"]
        for i, row in enumerate(top, 1):
            title = row.get("title", row.get("filename", "?"))
            lines.append(f"{i}. [Document: {title}]\n   {row['content'][:400]}")
        return "\n".join(lines)

    async def _handle_query_knowledge_graph(self, args: dict) -> str:
        from .tools_memory import _rank_by_similarity
        query = args.get("query", "").strip()
        if not query:
            return "Error: No query provided."
        mode = args.get("mode", "search")
        limit = int(args.get("limit", 5))

        if mode == "neighbors":
            row = self._db.get_kg_node_by_label(query)
            if not row:
                return f"No node found with label '{query}'. Try mode='search'."
            neighbors = self._db.get_kg_neighbors(row["id"])
            if not neighbors:
                return f"Node '{query}' has no connections."
            lines = [f"Connections for '{query}':\n"]
            for n in neighbors:
                arrow = (f"--[{n['relation']}]-->"
                         if n["direction"] == "out"
                         else f"<--[{n['relation']}]--")
                lines.append(f"  {query} {arrow} {n['label']}")
            return "\n".join(lines)

        # semantic search
        try:
            embed = self._get_embed_client()
            query_vec = await embed.embed(query)
        except Exception as e:
            return f"Embedding service unavailable: {e}"
        rows = self._db.get_kg_nodes_with_embeddings()
        if not rows:
            return "Knowledge graph is empty."
        top = _rank_by_similarity(query_vec, rows, limit)
        lines = [f"Knowledge graph nodes related to '{query}':\n"]
        for i, row in enumerate(top, 1):
            desc = f" — {row['description']}" if row.get("description") else ""
            lines.append(f"{i}. {row['label']}{desc}")
        return "\n".join(lines)

    # -----------------------------------------------------------------------
    # Tool handlers — write
    # -----------------------------------------------------------------------

    async def _handle_store_memory(self, args: dict) -> str:
        content = args.get("content", "").strip()
        if not content:
            return "Error: No content provided."
        entity_id = self._resolve_entity_id(int(args.get("entity_id", 0)))
        memory_id = str(uuid.uuid4())
        self._db.add_entity_memory(
            memory_id=memory_id, entity_id=entity_id,
            content=content, discussion_id=None,
        )
        # Embed in background
        try:
            from .tools_memory import _pack_embedding
            embed = self._get_embed_client()
            vec = await embed.embed(content)
            blob = _pack_embedding(vec)
            self._db.set_entity_memory_embedding(memory_id, blob)
        except Exception as e:
            logger.warning("Could not embed memory %s: %s", memory_id, e)
        return f"Memory stored (id: {memory_id})."

    async def _handle_delete_memory(self, args: dict) -> str:
        memory_id = args.get("memory_id", "").strip()
        if not memory_id:
            return "Error: No memory_id provided."
        # HARD RULE: can only delete own agent's memories
        agent_id = self._get_agent_entity_id()
        deleted = self._db.delete_entity_memory(memory_id, agent_id)
        if deleted:
            return f"Memory {memory_id} deleted."
        return "Memory not found or does not belong to this agent."

    async def _handle_assert_knowledge(self, args: dict) -> str:
        subject = args.get("subject", "").strip()
        relation = args.get("relation", "").strip()
        obj = args.get("object", "").strip()
        description = args.get("description", "").strip() or None
        if not subject or not relation or not obj:
            return "Error: subject, relation, and object are all required."
        # Upsert subject node
        subj_row = self._db.get_kg_node_by_label(subject)
        if not subj_row:
            subj_id = str(uuid.uuid4())
            self._db.upsert_kg_node(subj_id, subject, "concept", description)
        else:
            subj_id = subj_row["id"]
        # Upsert object node
        obj_row = self._db.get_kg_node_by_label(obj)
        if not obj_row:
            obj_id = str(uuid.uuid4())
            self._db.upsert_kg_node(obj_id, obj, "concept", None)
        else:
            obj_id = obj_row["id"]
        edge_id = str(uuid.uuid4())
        self._db.add_kg_edge(
            edge_id=edge_id, source_id=subj_id, target_id=obj_id,
            relation=relation, discussion_id=None,
        )
        # Embed nodes in background
        try:
            from .tools_memory import _pack_embedding
            embed = self._get_embed_client()
            for node_id, label in [(subj_id, subject), (obj_id, obj)]:
                vec = await embed.embed(label)
                blob = _pack_embedding(vec)
                self._db.set_kg_node_embedding(node_id, blob)
        except Exception as e:
            logger.warning("Could not embed KG nodes: %s", e)
        return f"Asserted: {subject} --[{relation}]--> {obj}"

    # -----------------------------------------------------------------------
    # Tool handler — run_discussion
    # -----------------------------------------------------------------------

    async def _handle_run_discussion(self, args: dict) -> str:
        from .app import ConsensusApp

        topic = args.get("topic", "").strip()
        if not topic:
            return "Error: No topic provided."
        entity_ids = args.get("entity_ids")
        max_rounds = int(args.get("max_rounds", 3))
        moderator_id = args.get("moderator_id")
        cost_limit = float(args.get("cost_limit", 1.0))

        # Resolve entities
        if not entity_ids:
            all_ai = self._db.get_entities(entity_type="ai")
            all_ai = [e for e in all_ai if e["name"] != AGENT_ENTITY_NAME]
            if len(all_ai) < 2:
                return ("Error: Need at least 2 AI entities configured. "
                        "Create them in the Consensus UI first.")
            entity_ids = [e["id"] for e in all_ai[:3]]

        # Validate all are AI
        for eid in entity_ids:
            entity = self._db.get_entity(eid)
            if not entity:
                return f"Error: Entity {eid} not found."
            if entity["entity_type"] == "human":
                return (f"Error: Entity '{entity['name']}' is human. "
                        "Only AI entities can participate in automated discussions.")

        if not moderator_id:
            moderator_id = entity_ids[0]

        # Create and configure a fresh ConsensusApp
        app = ConsensusApp(self._db.db_path)

        for eid in entity_ids:
            is_mod = (eid == moderator_id)
            app.add_to_discussion(eid, is_moderator=is_mod)

        app.set_topic(topic)

        mod_participates = len(entity_ids) <= 2
        result = app.start_discussion(
            moderator_participates=mod_participates,
            max_rounds=max_rounds,
            cost_limit=cost_limit,
        )
        if isinstance(result, dict) and "error" in result:
            return f"Error starting discussion: {result['error']}"

        # Run turn loop
        async def _run_turns():
            while app.discussion.is_active and app.discussion.status == "active":
                current = app.discussion.current_speaker
                if not current:
                    break
                if current.entity_type.value == "human":
                    break
                turn_result = await app.generate_ai_turn()
                if isinstance(turn_result, dict):
                    if "error" in turn_result and not turn_result.get("skipped"):
                        break
                complete_result = await app.complete_turn()
                if isinstance(complete_result, dict):
                    if (complete_result.get("max_rounds_reached")
                            or complete_result.get("cost_limit_reached")
                            or complete_result.get("method_complete")):
                        break

        try:
            await asyncio.wait_for(_run_turns(), timeout=600)
        except asyncio.TimeoutError:
            logger.warning("run_discussion timed out after 10 minutes")

        # Conclude
        try:
            await app.conclude_discussion()
        except Exception as e:
            logger.warning("Conclusion failed: %s", e)

        # Extract results
        conclusion = ""
        for m in reversed(app.discussion.messages):
            if "Final Synthesis" in m.content:
                conclusion = m.content
                break

        total_cost = sum(m.cost or 0 for m in app.discussion.messages)
        participant_names = [e.name for e in app.discussion.entities]

        return json.dumps({
            "discussion_id": app.discussion.id,
            "topic": topic,
            "conclusion": conclusion,
            "total_messages": len(app.discussion.messages),
            "total_cost": round(total_cost, 6),
            "participants": participant_names,
            "rounds_completed": app.discussion.current_round,
        }, indent=2)

    # -----------------------------------------------------------------------
    # JSON-RPC protocol
    # -----------------------------------------------------------------------

    def _make_response(self, msg_id: Any, result: Any) -> dict:
        return {"jsonrpc": JSONRPC_VERSION, "id": msg_id, "result": result}

    def _make_error(self, msg_id: Any, code: int, message: str) -> dict:
        return {
            "jsonrpc": JSONRPC_VERSION, "id": msg_id,
            "error": {"code": code, "message": message},
        }

    async def handle_request(self, msg: dict) -> Optional[dict]:
        method = msg.get("method")
        msg_id = msg.get("id")
        params = msg.get("params", {})

        if method == "initialize":
            return self._make_response(msg_id, {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            })

        if method == "notifications/initialized":
            return None  # notification — no response

        if method == "tools/list":
            tools = [t["definition"] for t in self._tools.values()]
            return self._make_response(msg_id, {"tools": tools})

        if method == "tools/call":
            return await self._handle_tool_call(msg_id, params)

        return self._make_error(msg_id, -32601, f"Method not found: {method}")

    async def _handle_tool_call(self, msg_id: Any, params: dict) -> dict:
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})
        tool = self._tools.get(tool_name)
        if not tool:
            return self._make_error(msg_id, -32602, f"Unknown tool: {tool_name}")
        try:
            result_text = await tool["handler"](arguments)
            return self._make_response(msg_id, {
                "content": [{"type": "text", "text": result_text}],
            })
        except Exception as e:
            logger.exception("Tool %s failed", tool_name)
            return self._make_response(msg_id, {
                "content": [{"type": "text", "text": f"Error: {e}"}],
                "isError": True,
            })

    # -----------------------------------------------------------------------
    # Main loop
    # -----------------------------------------------------------------------

    async def run(self) -> None:
        """Read JSON-RPC from stdin, dispatch, write responses to stdout.

        Uses synchronous stdin reads in a thread executor to avoid asyncio
        pipe transport issues on macOS/redirected pipes, while keeping tool
        handlers fully async.
        """
        loop = asyncio.get_event_loop()

        while True:
            line = await loop.run_in_executor(None, sys.stdin.readline)
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            response = await self.handle_request(msg)
            if response is not None:
                out = json.dumps(response) + "\n"
                sys.stdout.write(out)
                sys.stdout.flush()

    def close(self) -> None:
        self._db.close()


def main() -> None:
    load_env()
    logging.basicConfig(
        level=logging.INFO, stream=sys.stderr,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    server = ConsensusMCPServer()
    try:
        asyncio.run(server.run())
    except KeyboardInterrupt:
        pass
    finally:
        server.close()


if __name__ == "__main__":
    main()
