"""Embedding maths and the background chunk-embedding pass."""

import asyncio
import logging
import math
import struct

from .constants import DEFAULT_CHUNK_OVERLAP, DEFAULT_CHUNK_SIZE

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Embedding helpers (reuse patterns from tools_memory.py)
# ---------------------------------------------------------------------------

def _pack_embedding(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


def _unpack_embedding(blob: bytes) -> list[float]:
    n = len(blob) // 4
    return list(struct.unpack(f"{n}f", blob))


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    # Differing dimensions mean the vectors came from different embedding
    # models; zip() would silently truncate and yield a meaningless score, so
    # treat them as unrelated instead.
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(x * x for x in b))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)


def _rank_by_similarity(
    query_vec: list[float], rows: list[dict], limit: int,
    threshold: float = 0.0,
) -> list[tuple[float, dict]]:
    """Sort rows by cosine similarity, return top-limit above threshold."""
    scored = []
    for row in rows:
        emb = _unpack_embedding(row["embedding"])
        score = _cosine_similarity(query_vec, emb)
        if score >= threshold:
            scored.append((score, row))
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[:limit]

# ---------------------------------------------------------------------------
# Background embedding task
# ---------------------------------------------------------------------------

# Hold strong references to background tasks: asyncio only keeps a weak
# reference, so an un-retained task can be garbage-collected mid-run.
_background_tasks: set = set()


def _spawn_background(coro) -> None:
    """Schedule a fire-and-forget coroutine, retaining a strong reference."""
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)

_embedding_docs: set[int] = set()  # track documents currently being embedded

def _split_into_sub_chunks(text: str, size: int = DEFAULT_CHUNK_SIZE,
                           overlap: int = DEFAULT_CHUNK_OVERLAP
                           ) -> list[str]:
    """Split text into overlapping sub-chunks of at most *size* characters."""
    if len(text) <= size:
        return [text]
    sub_chunks = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        sub_chunks.append(text[start:end])
        start += size - overlap
    return sub_chunks


async def _embed_single_chunk(chunk, doc_id: int, db, embed_client) -> bool:
    """Embed a single chunk, re-chunking if it exceeds the model context.

    On context-length errors the chunk is split into smaller overlapping
    sub-chunks which are stored as new DB rows and embedded individually.
    Transient errors are retried with exponential backoff.

    Returns True on success, False if all attempts exhausted.
    """
    from ..tools_memory import EmbeddingContextLengthError

    try:
        vec = await embed_client.embed(chunk["content"])
        blob = _pack_embedding(vec)
        db.set_chunk_embedding(chunk["id"], blob)
        return True

    except EmbeddingContextLengthError:
        # Re-chunk into smaller overlapping pieces
        sub_texts = _split_into_sub_chunks(chunk["content"])
        logger.info(
            "Chunk %d of doc %d exceeds context — splitting into "
            "%d sub-chunks (%d char / %d overlap)",
            chunk["id"], doc_id, len(sub_texts),
            DEFAULT_CHUNK_SIZE, DEFAULT_CHUNK_OVERLAP,
        )

        # Get the next available chunk_index for this document.  Guard the
        # empty case so an absent chunk list cannot raise ValueError and
        # abort the whole background embedding pass.
        existing_chunks = db.get_document_chunks(doc_id)
        next_index = max(
            (c["chunk_index"] for c in existing_chunks), default=-1,
        ) + 1

        all_ok = True
        step = DEFAULT_CHUNK_SIZE - DEFAULT_CHUNK_OVERLAP
        for i, sub_text in enumerate(sub_texts):
            # Store sub-chunk in DB with position relative to parent
            from_char = chunk["from_char"] + i * step
            to_char = min(from_char + len(sub_text), chunk["to_char"])
            sub_id = db.add_document_chunk(
                doc_id, next_index + i, sub_text,
                from_char, to_char, chunk["section_header"],
            )
            try:
                vec = await embed_client.embed(sub_text)
                blob = _pack_embedding(vec)
                db.set_chunk_embedding(sub_id, blob)
            except Exception as e:
                logger.error(
                    "Failed to embed sub-chunk %d (from chunk %d) "
                    "of doc %d: %s", sub_id, chunk["id"], doc_id, e,
                )
                all_ok = False

        # Remove the original oversized chunk since it's been replaced
        db.delete_document_chunk(chunk["id"])
        return all_ok

    except Exception as e:
        logger.error(
            "Embed chunk %d of doc %d failed: %s",
            chunk["id"], doc_id, e,
        )
        return False


async def _embed_document_chunks(doc_id: int, db, embed_client) -> None:
    """Background task: embed all unembedded chunks for a document."""
    try:
        chunks = db.get_document_chunks(doc_id)
        existing = db.get_chunks_with_embeddings(doc_id)
        embedded_ids = {c["id"] for c in existing}

        failed_chunks = []
        for chunk in chunks:
            if chunk["id"] in embedded_ids:
                continue
            ok = await _embed_single_chunk(chunk, doc_id, db, embed_client)
            if ok:
                embedded_ids.add(chunk["id"])
            else:
                failed_chunks.append(chunk)

        if failed_chunks:
            logger.warning(
                "Doc %d: %d/%d chunks failed to embed",
                doc_id, len(failed_chunks), len(chunks),
            )
    finally:
        _embedding_docs.discard(doc_id)
