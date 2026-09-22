"""Document RAG tool provider for Consensus.

Provides AI participants with tools to interrogate reference documents:
- Add documents (by URL or inline text)
- List documents in the current discussion or full library
- Read document text, sections, chapters
- Ask questions with RAG-based retrieval
- Summarize documents or sections

Requires: sqlite-vec, numpy (optional dep group [memory])
Requires: ollama or cloud API for embeddings
Optional: pdfplumber for PDF parsing

Split out of the former single ``tools_document.py`` (issue #61, golden
rule 8) into layers that run leaf-first, so the internal import graph stays
acyclic:

``constants``
    Chunking, RAG, timeout and summarization tuning values. The leaf every
    other module may import.
``parsing``
    Bytes → markdown (PDF, HTML, text) plus URL fetching and markdown
    section extraction.
``chunking``
    Paragraph-aware splitting of markdown into overlapping chunks.
``embedding``
    Cosine/ranking maths and the background chunk-embedding pass, including
    the re-chunking retry for chunks that exceed the model context.
``schemas``
    JSON parameter schemas for the eight ``doc_*`` tools.
``llm``
    The interpretation-LLM helper used for summaries and RAG answers.
``ingestion``
    The parse → chunk → store → embed pipeline.
``handlers``
    The eight ``doc_*`` tool handlers.
``provider``
    Assembles the handlers and schemas into a ``PythonToolProvider``.

Only the public API is re-exported here, so ``from consensus.tools_document
import create_document_provider`` keeps working exactly as before the split.
Internal helpers are reached through their defining submodule — which is also
where tests must aim ``patch()``, since patching a name on this facade would
not intercept the reference the code actually uses.
"""

from .chunking import chunk_document
from .ingestion import ingest_document
from .parsing import extract_sections, fetch_url_content, parse_document
from .provider import create_document_provider

__all__ = [
    "chunk_document",
    "create_document_provider",
    "extract_sections",
    "fetch_url_content",
    "ingest_document",
    "parse_document",
]
