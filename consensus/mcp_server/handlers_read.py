"""Read-only tool handlers: listing, reading and semantic search."""

from __future__ import annotations

import json


class ReadHandlersMixin:
    """Handlers that read discussions, entities, documents, memories and the
    knowledge graph without changing them."""

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
        from .. import app_discussion_state
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
        from ..tools_memory import _rank_by_similarity
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
        from ..tools_memory import _rank_by_similarity, backfill_embeddings
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
        await backfill_embeddings(self._db.get_unembedded_entity_memories(entity_id),
                                  embed, self._db.set_entity_memory_embedding, "memory")
        rows = self._db.get_entity_memories_with_embeddings(entity_id)
        if not rows:
            return "No memories found for this entity."
        top = _rank_by_similarity(query_vec, rows, limit)
        lines = [f"Recalled {len(top)} memories:\n"]
        for i, row in enumerate(top, 1):
            lines.append(f"{i}. [{row['id']}] {row['content']}")
        return "\n".join(lines)

    async def _handle_search_documents(self, args: dict) -> str:
        from ..tools_memory import _rank_by_similarity
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
        from ..tools_memory import _rank_by_similarity, backfill_embeddings
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
        await backfill_embeddings(self._db.get_unembedded_kg_nodes(), embed,
                                  self._db.set_kg_node_embedding, "kg node")
        rows = self._db.get_kg_nodes_with_embeddings()
        if not rows:
            return "Knowledge graph is empty."
        top = _rank_by_similarity(query_vec, rows, limit)
        lines = [f"Knowledge graph nodes related to '{query}':\n"]
        for i, row in enumerate(top, 1):
            desc = f" — {row['description']}" if row.get("description") else ""
            lines.append(f"{i}. {row['label']}{desc}")
        return "\n".join(lines)
