"""Embedding client and vector helpers for the institutional memory tools.

Requires: ollama running locally with an embedding model (see models.DEFAULT_EMBEDDING_MODEL)
"""

import asyncio
import logging
import math
import struct

import httpx

from ..models import DEFAULT_EMBEDDING_ENDPOINT, DEFAULT_EMBEDDING_MODEL
from .constants import EMBED_BASE_DELAY, EMBED_MAX_RETRIES, EMBED_TIMEOUT

logger = logging.getLogger(__name__)


class MemoryUnavailableError(Exception):
    """Raised when the embedding service is unreachable or persistently failing."""


class EmbeddingContextLengthError(Exception):
    """Raised when input text exceeds the embedding model's context window."""


class EmbeddingClient:
    """Thin async client for the ollama embeddings endpoint."""

    def __init__(self, db) -> None:
        self._db = db

    def _get_config(self) -> dict:
        """Return the stored embedding config, or the defaults if it is unreadable."""
        try:
            return self._db.get_memory_config()
        except Exception as e:
            # Visible, or the user's endpoint silently becomes the default one.
            logger.error("Could not read embedding config; using defaults: %s", e, exc_info=e)
            return {
                "embedding_endpoint": DEFAULT_EMBEDDING_ENDPOINT,
                "embedding_model": DEFAULT_EMBEDDING_MODEL,
            }

    async def embed(self, text: str) -> list[float]:
        """Return a float embedding vector for the given text.

        Makes up to EMBED_MAX_RETRIES attempts, with exponential backoff
        between them, on timeouts and 5xx responses; connection and other
        transport errors fail immediately.
        """
        config = self._get_config()
        endpoint = config.get("embedding_endpoint", DEFAULT_EMBEDDING_ENDPOINT)
        model = config.get("embedding_model", DEFAULT_EMBEDDING_MODEL)
        url = endpoint.rstrip("/") + "/api/embeddings"

        last_exc: Exception | None = None
        for attempt in range(EMBED_MAX_RETRIES):
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
            except (httpx.TimeoutException, httpx.HTTPStatusError) as e:
                last_exc = e
                if isinstance(e, httpx.HTTPStatusError) and e.response.status_code < 500:
                    raise MemoryUnavailableError(
                        f"Embedding request failed: {e}"
                    ) from e
                # Detect non-transient context length errors from Ollama
                if isinstance(e, httpx.HTTPStatusError) and e.response.status_code == 500:
                    try:
                        body = e.response.json()
                    except ValueError:
                        body = None  # not JSON — treat as transient
                    error = body.get("error") if isinstance(body, dict) else None
                    if isinstance(error, str) and "context length" in error:
                        raise EmbeddingContextLengthError(
                            f"Input exceeds embedding model context: {error}"
                        ) from e
                if attempt == EMBED_MAX_RETRIES - 1:
                    break  # sleeping now would only delay the raise below
                delay = EMBED_BASE_DELAY * (2 ** attempt)
                logger.warning(
                    "Embedding attempt %d/%d failed (%s), retrying in %.1fs",
                    attempt + 1, EMBED_MAX_RETRIES, e, delay,
                )
                await asyncio.sleep(delay)
            except httpx.HTTPError as e:  # remaining transport faults (read/protocol errors)
                raise MemoryUnavailableError(
                    f"Embedding request failed: {e}"
                ) from e
            except Exception as e:
                # A malformed response or a bug, not an outage: callers log
                # MemoryUnavailableError as a routine warning, so keep the traceback here.
                logger.exception("Unexpected embedding failure from %s", endpoint)
                raise MemoryUnavailableError(
                    f"Embedding request failed: {e}"
                ) from e

        raise MemoryUnavailableError(
            f"Embedding request failed after {EMBED_MAX_RETRIES} attempts: {last_exc}"
        ) from last_exc

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
    """Serialise a vector as packed float32 for a BLOB column."""
    return struct.pack(f"{len(vec)}f", *vec)


def _unpack_embedding(blob: bytes) -> list[float]:
    """Inverse of :func:`_pack_embedding`."""
    n = len(blob) // 4
    return list(struct.unpack(f"{n}f", blob))


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity of two vectors; 0.0 for zero or mismatched vectors."""
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

