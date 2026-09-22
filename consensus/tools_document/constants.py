"""Tuning constants for the document RAG pipeline.

Kept in their own module so that every other module in the package can import
them without depending on the package ``__init__``.
"""

# Chunking defaults
DEFAULT_CHUNK_SIZE = 500  # characters
DEFAULT_CHUNK_OVERLAP = 100  # characters

# RAG defaults
RAG_TOP_K = 5
MIN_SIMILARITY_THRESHOLD = 0.3

# Timeout for URL fetching
URL_FETCH_TIMEOUT = 30.0

# Timeout for interpretation LLM calls
LLM_TIMEOUT = 120.0

# Max chars to send to LLM for summarization in a single call
SUMMARY_CHUNK_LIMIT = 4000
