"""Institutional memory tool provider for Consensus.

Provides AI participants with persistent memory across discussions:
- Long-term personal memory (store, recall, forget observations/positions)
- Semantic search over past discussion messages
- Knowledge graph (assert and query concept/relationship triples)

Requires: sqlite-vec, numpy (default dependencies)
Requires: ollama running locally with an embedding model (see models.DEFAULT_EMBEDDING_MODEL)

The package is split by concern (issue #61): ``constants``, ``embedding``
(client + vector helpers), ``schemas``, ``handlers`` (personal memory and
discussion search), ``handlers_kg`` and ``provider``. Production code imports
only the names below; tests import private helpers from their submodule.
"""

from .embedding import (
    EmbeddingClient, EmbeddingContextLengthError, MemoryUnavailableError,
    _pack_embedding, _rank_by_similarity,
)
from .handlers import ensure_discussion_indexed
from .provider import create_memory_provider

__all__ = [
    "EmbeddingClient",
    "EmbeddingContextLengthError",
    "MemoryUnavailableError",
    "_pack_embedding",
    "_rank_by_similarity",
    "create_memory_provider",
    "ensure_discussion_indexed",
]
