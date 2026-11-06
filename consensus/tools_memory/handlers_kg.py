"""Handlers for the knowledge-graph tools (kg_assert, kg_query)."""

import logging
import uuid

from ..background import spawn_background
from ..tools import ToolContext, ToolResult
from .constants import SEARCH_DEFAULT_LIMIT
from .embedding import EmbeddingClient, MemoryUnavailableError, _pack_embedding, _rank_by_similarity
from .handlers import _tool_failure, backfill_embeddings
from .schemas import KG_QUERY_MODES

logger = logging.getLogger(__name__)


async def _kg_assert_handler(
    arguments: dict, context: ToolContext,
    db, embed_client: EmbeddingClient,
) -> ToolResult:
    """kg_assert: record a subject --relation--> object triple, creating nodes as needed."""
    subject = arguments.get("subject", "").strip()
    relation = arguments.get("relation", "").strip()
    obj = arguments.get("object", "").strip()
    description = arguments.get("description", "").strip() or None

    if not subject or not relation or not obj:
        return ToolResult(
            content="subject, relation, and object are all required.",
            is_error=True,
        )

    try:
        # Upsert subject node
        subj_row = db.get_kg_node_by_label(subject)
        if not subj_row:
            subj_id = str(uuid.uuid4())
            db.upsert_kg_node(subj_id, subject, "concept", description)
        else:
            subj_id = subj_row["id"]

        # Upsert object node
        obj_row = db.get_kg_node_by_label(obj)
        if not obj_row:
            obj_id = str(uuid.uuid4())
            db.upsert_kg_node(obj_id, obj, "concept", None)
        else:
            obj_id = obj_row["id"]

        edge_id = str(uuid.uuid4())
        db.add_kg_edge(
            edge_id=edge_id,
            source_id=subj_id,
            target_id=obj_id,
            relation=relation,
            discussion_id=context.discussion_id,
        )
    except Exception as e:
        return _tool_failure("Failed to assert knowledge triple", e)

    # Embed nodes in background
    async def _embed_nodes():
        for node_id, label in [(subj_id, subject), (obj_id, obj)]:
            try:
                vec = await embed_client.embed(label)
                blob = _pack_embedding(vec)
                db.set_kg_node_embedding(node_id, blob)
            except MemoryUnavailableError as e:
                logger.warning(
                    "Could not embed kg nodes %r/%r: %s", subject, obj, e,
                )
                break
            except Exception as e:
                logger.error("Failed to embed kg node %s: %s", node_id, e, exc_info=e)

    spawn_background(_embed_nodes(), f"embed kg nodes {subject!r}/{obj!r}")

    return ToolResult(
        content=f"Asserted: {subject} --[{relation}]--> {obj}",
        metadata={"subject": subject, "relation": relation, "object": obj},
    )


async def _kg_query_handler(
    arguments: dict, context: ToolContext,
    db, embed_client: EmbeddingClient,
) -> ToolResult:
    """kg_query: semantic node search, or the edges of one exactly-named node."""
    query = arguments.get("query", "").strip()
    mode = arguments.get("mode", "search")

    if not query:
        return ToolResult(content="No query provided.", is_error=True)
    if mode not in KG_QUERY_MODES:
        return ToolResult(
            content=f"Unknown mode {mode!r}; use one of: {', '.join(KG_QUERY_MODES)}.",
            is_error=True,
        )

    if mode == "neighbors":
        try:
            row = db.get_kg_node_by_label(query)  # exact label match
            neighbors = db.get_kg_neighbors(row["id"]) if row else []
        except Exception as e:
            return _tool_failure("Knowledge graph query failed", e)
        if not row:
            return ToolResult(
                content=f"No node found with label '{query}'. Try mode='search' first.",
            )

        if not neighbors:
            return ToolResult(content=f"Node '{query}' has no connections yet.")

        lines = [f"Connections for '{query}':\n"]
        for n in neighbors:
            arrow = f"--[{n['relation']}]-->" if n["direction"] == "out" else f"<--[{n['relation']}]--"
            lines.append(f"  {query} {arrow} {n['label']} (weight: {n['weight']:.2f})")
        return ToolResult(content="\n".join(lines), metadata={"count": len(neighbors)})

    else:  # search
        try:
            query_vec = await embed_client.embed(query)
        except MemoryUnavailableError as e:
            return _tool_failure("Memory service unavailable", e)

        try:
            await backfill_embeddings(db.get_unembedded_kg_nodes(), embed_client,
                                      db.set_kg_node_embedding, "kg node")
            rows = db.get_kg_nodes_with_embeddings()
        except Exception as e:
            return _tool_failure("Knowledge graph query failed", e)

        if not rows:
            return ToolResult(content="Knowledge graph is empty.")

        top = _rank_by_similarity(query_vec, rows, SEARCH_DEFAULT_LIMIT)
        lines = [f"Knowledge graph nodes related to '{query}':\n"]
        for i, row in enumerate(top, 1):
            desc = f" — {row['description']}" if row.get("description") else ""
            lines.append(f"{i}. {row['label']}{desc}")
        return ToolResult(content="\n".join(lines), metadata={"count": len(top)})
