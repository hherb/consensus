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

The same trap runs the other way, and it is the direction that bites the
*public* names. Before the split there was one binding per name, so a single
``patch("consensus.tools_document.ingest_document")`` caught every caller.
Now ``ingest_document`` is bound three times — here, in ``ingestion``, and in
``handlers`` — and ``from x import y`` copies the reference, so patching any
one of them leaves the other two pointing at the original. Patch the binding
in the module that will *execute* (``document_helpers.patch_where_defined``
resolves it from an anchor function), or patch every binding.
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
