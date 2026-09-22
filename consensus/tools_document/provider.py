"""Factory assembling the document RAG tool provider."""

import logging

from ..tools import PythonToolProvider, ToolContext, ToolDefinition, ToolResult
from .handlers import (
    _doc_add_handler, _doc_get_chapter_handler, _doc_get_length_handler,
    _doc_get_sections_handler, _doc_get_text_handler, _doc_list_handler,
)
from .handlers_rag import _doc_ask_handler, _doc_summary_handler
from .schemas import (
    _DOC_ADD_SCHEMA, _DOC_ASK_SCHEMA, _DOC_CHAPTER_SCHEMA, _DOC_LENGTH_SCHEMA,
    _DOC_LIST_SCHEMA, _DOC_SECTIONS_SCHEMA, _DOC_SUMMARY_SCHEMA, _DOC_TEXT_SCHEMA,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Provider factory
# ---------------------------------------------------------------------------

def create_document_provider(db, app=None) -> PythonToolProvider:
    """Create and return the document RAG tool provider.

    Args:
        db: Database instance for document storage.
        app: ConsensusApp instance for AI client access (needed by doc_ask, doc_summary).
    """
    # Import EmbeddingClient from tools_memory (reuse existing embedding infra)
    from ..tools_memory import EmbeddingClient
    embed_client = EmbeddingClient(db)

    provider = PythonToolProvider(name="documents")

    def _make_handler(fn):
        async def handler(arguments: dict, context: ToolContext) -> ToolResult:
            return await fn(arguments, context, db, embed_client, app)
        return handler

    provider.register(
        ToolDefinition(
            name="doc_add",
            description=(
                "Add a document to the current discussion for analysis. "
                "Provide a 'url' to fetch a web page or PDF, or 'text' for inline content. "
                "The document will be indexed for search and available to all participants."
            ),
            parameters=_DOC_ADD_SCHEMA,
        ),
        _make_handler(_doc_add_handler),
    )

    provider.register(
        ToolDefinition(
            name="doc_list",
            description=(
                "List documents available in this discussion. Returns title, summary, and ID "
                "for each document. Use full_library=true with a query to search all documents "
                "across all discussions by semantic similarity."
            ),
            parameters=_DOC_LIST_SCHEMA,
        ),
        _make_handler(_doc_list_handler),
    )

    provider.register(
        ToolDefinition(
            name="doc_get_length",
            description="Get the character count of a document.",
            parameters=_DOC_LENGTH_SCHEMA,
        ),
        _make_handler(_doc_get_length_handler),
    )

    provider.register(
        ToolDefinition(
            name="doc_get_text",
            description=(
                "Get a slice of the document's text by character range. "
                "Use from_char=0 and to_char=-1 to get the full text."
            ),
            parameters=_DOC_TEXT_SCHEMA,
        ),
        _make_handler(_doc_get_text_handler),
    )

    provider.register(
        ToolDefinition(
            name="doc_get_sections",
            description=(
                "Get the list of section/chapter headers in a document with their "
                "character offsets. Use this to navigate the document structure."
            ),
            parameters=_DOC_SECTIONS_SCHEMA,
        ),
        _make_handler(_doc_get_sections_handler),
    )

    provider.register(
        ToolDefinition(
            name="doc_get_chapter",
            description=(
                "Get the full text of a named section/chapter, including "
                "all of its subsections. Uses fuzzy matching on the header "
                "text."
            ),
            parameters=_DOC_CHAPTER_SCHEMA,
        ),
        _make_handler(_doc_get_chapter_handler),
    )

    provider.register(
        ToolDefinition(
            name="doc_ask",
            description=(
                "Ask a question about a document. Uses RAG (retrieval-augmented generation) "
                "to find relevant passages and generate an answer. Returns the answer plus "
                "the relevant passages with character offsets. Use doc_list first to find "
                "the document_id."
            ),
            parameters=_DOC_ASK_SCHEMA,
        ),
        _make_handler(_doc_ask_handler),
    )

    provider.register(
        ToolDefinition(
            name="doc_summary",
            description=(
                "Get a summary of a document or a character range within it. "
                "For long documents, uses map-reduce summarization."
            ),
            parameters=_DOC_SUMMARY_SCHEMA,
        ),
        _make_handler(_doc_summary_handler),
    )

    return provider
