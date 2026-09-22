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

# Sampling temperature for interpretation LLM calls (summaries, RAG answers)
INTERPRETATION_TEMPERATURE = 0.3

# Max chars to send to LLM for summarization in a single call
SUMMARY_CHUNK_LIMIT = 4000

# Max chars of a document sent to the LLM when generating its summary
SUMMARY_EXCERPT_CHARS = 3000

# Max chunks considered when searching the whole document library
LIBRARY_SEARCH_LIMIT = 20

# Max chars of a document summary shown in a doc_list entry
SUMMARY_SNIPPET_CHARS = 150

# Max section headers listed when a doc_get_chapter header does not match
AVAILABLE_HEADERS_HINT = 10

# Max chars of each retrieved passage returned by doc_ask
PASSAGE_PREVIEW_CHARS = 500
