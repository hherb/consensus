"""Document RAG tool provider for Consensus.

Provides AI participants with tools to interrogate reference documents:
- Add documents (by URL or inline text)
- List documents in the current discussion or full library
- Read document text, sections, chapters
- Ask questions with RAG-based retrieval
- Summarize documents or sections

Requires: sqlite-vec, numpy, pdfplumber, trafilatura — all default
dependencies since the alpha releases; ``[memory]`` is an empty alias extra
kept for backwards compatibility (see ``pyproject.toml``).
Requires: ollama or a cloud API for embeddings
Optional: PyPDF2 as a secondary PDF backend

Split out of the former single ``tools_document.py`` (issue #61, golden
rule 8) into layers that run leaf-first, so the internal import graph stays
acyclic:

``constants``
    Chunking, RAG, timeout and summarization tuning values, plus aliases for
    ``models.SummaryStatus`` (its only import, from outside the package, so
    it remains the leaf every other module here may import).
``errors``
    ``DocumentError`` and its ``DocumentParseError`` /
    ``DocumentInterpretationError`` / ``DocumentIndexError`` subclasses, each
    carrying an actionable ``hint``. A leaf, imported wherever a failure
    needs a type instead of a string.
``schemas``
    JSON parameter schemas for the eight ``doc_*`` tools.
``validation``
    Pure ``resolve_range()`` / ``chapter_range()`` helpers for model-supplied
    character ranges. Another leaf, with no dependency on the rest of the
    package.
``parsing``
    Bytes → markdown (PDF, HTML, text) plus URL fetching and markdown
    section extraction.
``chunking``
    Paragraph-aware splitting of markdown into overlapping chunks.
``embedding``
    Cosine/ranking maths and the background chunk-embedding pass, including
    the re-chunking retry for chunks that exceed the model context. Imports
    ``errors`` for ``DocumentIndexError`` and, from outside the package,
    ``consensus.dbkey`` for the session-scoped key behind ``doc_key``.
``llm``
    The interpretation-LLM helper used for summaries and RAG answers.
``ingestion``
    The parse → chunk → store → embed pipeline.
``handlers_rag``
    The ``doc_ask`` and ``doc_summary`` handlers. Split out from
    ``handlers`` once that module crossed the 500-line rule (issue #78).
``handlers``
    The six non-RAG ``doc_*`` tool handlers (add/list/length/text/sections/
    chapter); imports only ``_reindex_message`` from ``handlers_rag``, to
    report a chunk-dimension mismatch the same way ``doc_ask`` does.
``provider``
    Imports all eight handlers directly — six from ``handlers``, ``doc_ask``
    and ``doc_summary`` from ``handlers_rag`` — and the schemas, assembling
    them into a ``PythonToolProvider``.

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
