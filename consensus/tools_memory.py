"""Institutional memory tool provider for Consensus.

Provides AI participants with persistent memory across discussions:
- Long-term personal memory (store, recall, forget observations/positions)
- Semantic search over past discussion messages
- Knowledge graph (assert and query concept/relationship triples)

Requires: sqlite-vec, numpy (optional dep group [memory])
Requires: ollama running locally with an embedding model (default: nomic-embed-text)
"""

import asyncio
import json
import logging
import struct
import time
import uuid
from typing import Optional

import httpx

from .tools import PythonToolProvider, ToolContext, ToolDefinition, ToolResult

logger = logging.getLogger(__name__)

EMBED_TIMEOUT = 10.0
EMBED_DIM = 768  # nomic-embed-text default
SEARCH_DEFAULT_LIMIT = 5
INDEX_BATCH_SIZE = 32


class MemoryUnavailableError(Exception):
    """Raised when the embedding backend is unreachable."""


class EmbeddingClient:
    """Thin async client for the ollama embeddings endpoint."""

    def __init__(self, db) -> None:
        self._db = db

    def _get_config(self) -> dict:
        try:
            return self._db.get_memory_config()
        except Exception:
            return {
                "embedding_endpoint": "http://localhost:11434",
                "embedding_model": "nomic-embed-text",
            }

    async def embed(self, text: str) -> list[float]:
        """Return a float embedding vector for the given text."""
        config = self._get_config()
        endpoint = config.get("embedding_endpoint", "http://localhost:11434")
        model = config.get("embedding_model", "nomic-embed-text")
        url = endpoint.rstrip("/") + "/api/embeddings"

        try:
            async with httpx.AsyncClient(timeout=EMBED_TIMEOUT) as client:
                response = await client.post(
                    url,
                    json={"model": model, "prompt": text},
                )
                response.raise_for_status()
                data = response.json()
                return data["embedding"]
        except httpx.ConnectError as e:
            raise MemoryUnavailableError(
                f"Cannot connect to embedding service at {endpoint}: {e}"
            ) from e
        except Exception as e:
            raise MemoryUnavailableError(
                f"Embedding request failed: {e}"
            ) from e

    async def test_connection(self) -> tuple[bool, str]:
        """Return (success, message) for connection test."""
        try:
            vec = await self.embed("test")
            return True, f"Connected. Embedding dim: {len(vec)}"
        except MemoryUnavailableError as e:
            return False, str(e)


# ---------------------------------------------------------------------------
# Serialisation helpers for embedding blobs
# ---------------------------------------------------------------------------

def _pack_embedding(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


def _unpack_embedding(blob: bytes) -> list[float]:
    n = len(blob) // 4
    return list(struct.unpack(f"{n}f", blob))


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    import math
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(x * x for x in b))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)


def _rank_by_similarity(query_vec: list[float],
                        rows: list[dict],
                        limit: int) -> list[dict]:
    """Sort rows by cosine similarity to query_vec, return top-limit."""
    scored = []
    for row in rows:
        emb = _unpack_embedding(row["embedding"])
        score = _cosine_similarity(query_vec, emb)
        scored.append((score, row))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [row for _, row in scored[:limit]]


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

_KG_QUERY_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "Concept label to look up, or a search phrase for semantic mode.",
        },
        "mode": {
            "type": "string",
            "enum": ["search", "neighbors", "path"],
            "description": (
                "'search': find nodes semantically similar to query; "
                "'neighbors': return edges connected to the matched node; "
                "'path': not yet implemented (reserved)."
            ),
            "default": "search",
        },
        "target": {
            "type": "string",
            "description": "For 'path' mode: the destination node label.",
        },
    },
    "required": ["query"],
}


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------

async def _memory_store_handler(
    arguments: dict, context: ToolContext,
    db, embed_client: EmbeddingClient,
) -> ToolResult:
    content = arguments.get("content", "").strip()
    if not content:
        return ToolResult(content="No content provided.", is_error=True)

    memory_id = str(uuid.uuid4())
    discussion_id = context.discussion_id if context.discussion_id else None

    try:
        db.add_entity_memory(
            memory_id=memory_id,
            entity_id=context.caller_entity_id,
            content=content,
            discussion_id=discussion_id,
        )
    except Exception as e:
        return ToolResult(content=f"Failed to store memory: {e}", is_error=True)

    # Embed asynchronously in background — don't block the turn
    async def _embed_and_store():
        try:
            vec = await embed_client.embed(content)
            blob = _pack_embedding(vec)
            db.set_entity_memory_embedding(memory_id, blob)
        except MemoryUnavailableError as e:
            logger.warning("Could not embed memory %s: %s", memory_id, e)
        except Exception as e:
            logger.warning("Unexpected error embedding memory %s: %s", memory_id, e)

    asyncio.create_task(_embed_and_store())

    return ToolResult(
        content=f"Memory stored (id: {memory_id}).",
        metadata={"memory_id": memory_id},
    )


async def _memory_recall_handler(
    arguments: dict, context: ToolContext,
    db, embed_client: EmbeddingClient,
) -> ToolResult:
    query = arguments.get("query", "").strip()
    if not query:
        return ToolResult(content="No query provided.", is_error=True)
    limit = int(arguments.get("limit", SEARCH_DEFAULT_LIMIT))

    try:
        query_vec = await embed_client.embed(query)
    except MemoryUnavailableError as e:
        return ToolResult(content=f"Memory service unavailable: {e}", is_error=True)

    try:
        rows = db.get_entity_memories_with_embeddings(context.caller_entity_id)
    except Exception as e:
        return ToolResult(content=f"Failed to retrieve memories: {e}", is_error=True)

    if not rows:
        return ToolResult(content="No memories stored yet.")

    top = _rank_by_similarity(query_vec, rows, limit)

    lines = [f"Recalled {len(top)} memories for '{query}':\n"]
    for i, row in enumerate(top, 1):
        lines.append(f"{i}. [{row['id']}] {row['content']}")

    return ToolResult(content="\n".join(lines), metadata={"count": len(top)})


async def _memory_forget_handler(
    arguments: dict, context: ToolContext,
    db, embed_client: EmbeddingClient,
) -> ToolResult:
    memory_id = arguments.get("memory_id", "").strip()
    if not memory_id:
        return ToolResult(content="No memory_id provided.", is_error=True)

    try:
        deleted = db.delete_entity_memory(memory_id, context.caller_entity_id)
    except Exception as e:
        return ToolResult(content=f"Failed to delete memory: {e}", is_error=True)

    if deleted:
        return ToolResult(content=f"Memory {memory_id} deleted.")
    return ToolResult(
        content=f"Memory {memory_id} not found or does not belong to this entity.",
        is_error=True,
    )


async def _discussion_search_handler(
    arguments: dict, context: ToolContext,
    db, embed_client: EmbeddingClient,
) -> ToolResult:
    query = arguments.get("query", "").strip()
    if not query:
        return ToolResult(content="No query provided.", is_error=True)
    limit = int(arguments.get("limit", SEARCH_DEFAULT_LIMIT))
    topic_filter: Optional[str] = arguments.get("topic_filter")

    # Lazy-index the current discussion's messages if needed
    try:
        unindexed = db.get_unindexed_message_ids(context.discussion_id)
        if unindexed:
            asyncio.create_task(_index_messages(unindexed, db, embed_client))
    except Exception as e:
        logger.warning("Could not check unindexed messages: %s", e)

    try:
        query_vec = await embed_client.embed(query)
    except MemoryUnavailableError as e:
        return ToolResult(content=f"Memory service unavailable: {e}", is_error=True)

    try:
        rows = db.get_messages_with_embeddings(topic_filter)
    except Exception as e:
        return ToolResult(content=f"Failed to search discussions: {e}", is_error=True)

    if not rows:
        return ToolResult(content="No indexed discussion messages found yet.")

    top = _rank_by_similarity(query_vec, rows, limit)

    lines = [f"Found {len(top)} relevant passages for '{query}':\n"]
    for i, row in enumerate(top, 1):
        topic = row.get("topic", "unknown topic")
        lines.append(f"{i}. [Discussion: {topic}]\n   {row['content'][:300]}")

    return ToolResult(content="\n".join(lines), metadata={"count": len(top)})


async def _index_messages(
    message_ids: list[str],
    db,
    embed_client: EmbeddingClient,
) -> None:
    """Background task: embed and store message embeddings."""
    for msg_id in message_ids:
        try:
            content = db.get_message_content(msg_id)
            if not content:
                continue
            vec = await embed_client.embed(content[:1000])
            blob = _pack_embedding(vec)
            db.set_message_embedding(msg_id, blob)
        except MemoryUnavailableError:
            break  # Stop if service is down
        except Exception as e:
            logger.warning("Failed to index message %s: %s", msg_id, e)


async def _kg_assert_handler(
    arguments: dict, context: ToolContext,
    db, embed_client: EmbeddingClient,
) -> ToolResult:
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
        return ToolResult(content=f"Failed to assert knowledge triple: {e}", is_error=True)

    # Embed nodes in background
    async def _embed_nodes():
        for node_id, label in [(subj_id, subject), (obj_id, obj)]:
            try:
                vec = await embed_client.embed(label)
                blob = _pack_embedding(vec)
                db.set_kg_node_embedding(node_id, blob)
            except MemoryUnavailableError:
                break
            except Exception as e:
                logger.warning("Failed to embed kg node %s: %s", node_id, e)

    asyncio.create_task(_embed_nodes())

    return ToolResult(
        content=f"Asserted: {subject} --[{relation}]--> {obj}",
        metadata={"subject": subject, "relation": relation, "object": obj},
    )


async def _kg_query_handler(
    arguments: dict, context: ToolContext,
    db, embed_client: EmbeddingClient,
) -> ToolResult:
    query = arguments.get("query", "").strip()
    mode = arguments.get("mode", "search")
    target = arguments.get("target", "").strip()

    if not query:
        return ToolResult(content="No query provided.", is_error=True)

    if mode == "neighbors":
        # Exact label match first
        row = db.get_kg_node_by_label(query)
        if not row:
            return ToolResult(
                content=f"No node found with label '{query}'. Try mode='search' first.",
            )
        try:
            neighbors = db.get_kg_neighbors(row["id"])
        except Exception as e:
            return ToolResult(content=f"Knowledge graph query failed: {e}", is_error=True)

        if not neighbors:
            return ToolResult(content=f"Node '{query}' has no connections yet.")

        lines = [f"Connections for '{query}':\n"]
        for n in neighbors:
            arrow = f"--[{n['relation']}]-->" if n["direction"] == "out" else f"<--[{n['relation']}]--"
            lines.append(f"  {query} {arrow} {n['label']} (weight: {n['weight']:.2f})")
        return ToolResult(content="\n".join(lines), metadata={"count": len(neighbors)})

    elif mode == "path":
        return ToolResult(
            content="Path queries are not yet implemented.",
            is_error=False,
        )

    else:  # search
        try:
            query_vec = await embed_client.embed(query)
        except MemoryUnavailableError as e:
            return ToolResult(content=f"Memory service unavailable: {e}", is_error=True)

        try:
            rows = db.get_kg_nodes_with_embeddings()
        except Exception as e:
            return ToolResult(content=f"Knowledge graph query failed: {e}", is_error=True)

        if not rows:
            return ToolResult(content="Knowledge graph is empty.")

        top = _rank_by_similarity(query_vec, rows, 5)
        lines = [f"Knowledge graph nodes related to '{query}':\n"]
        for i, row in enumerate(top, 1):
            desc = f" — {row['description']}" if row.get("description") else ""
            lines.append(f"{i}. {row['label']}{desc}")
        return ToolResult(content="\n".join(lines), metadata={"count": len(top)})


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
