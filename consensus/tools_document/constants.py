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

# Summary generation outcome recorded in documents.summary_status
SUMMARY_STATUS_OK = "ok"
SUMMARY_STATUS_FAILED = "failed"
SUMMARY_STATUS_PENDING = "pending"

# Extraction fidelity reported by parse_document
FIDELITY_FULL = "full"
FIDELITY_DEGRADED = "degraded"

# Above this share of U+FFFD replacement characters, a "text" document is
# really binary that was decoded with errors="replace" (issue #78 defect 7).
MAX_REPLACEMENT_CHAR_RATIO = 0.1

# URL fetching retry policy (golden rule 5)
URL_FETCH_MAX_RETRIES = 3
URL_FETCH_BASE_DELAY = 1.0  # seconds, doubled per attempt

# Largest document accepted from a URL, in bytes. Checked against the
# content-length header and again while the body streams in, so a
# header-less (chunked) response is aborted mid-transfer instead of being
# read fully into memory first.
MAX_DOCUMENT_BYTES = 50 * 1024 * 1024

# Minimum seconds between background embedding retries for a document whose
# last indexing pass failed. doc_ask re-kicks the pass so a transient
# embedder outage recovers by itself, and this interval is what stops every
# doc_ask call from hammering a service that is still down.
INDEXING_RETRY_INTERVAL = 60.0
