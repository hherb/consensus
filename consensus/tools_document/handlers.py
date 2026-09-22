"""Handlers behind the eight ``doc_*`` tools."""

import json
import logging
from typing import Optional

from ..tools import ToolContext, ToolResult
from .constants import (
    AVAILABLE_HEADERS_HINT, LIBRARY_SEARCH_LIMIT, MIN_SIMILARITY_THRESHOLD,
    SUMMARY_SNIPPET_CHARS, SUMMARY_STATUS_FAILED, SUMMARY_STATUS_OK,
)
from .embedding import _rank_by_similarity
from .errors import DocumentError
from .handlers_rag import _reindex_message
from .ingestion import ingest_document
from .parsing import fetch_url_content
from .validation import resolve_range

logger = logging.getLogger(__name__)


def _summary_snippet(summary: Optional[str], status: str) -> str:
    """Render a document summary for a one-line ``doc_list`` entry.

    A document with no usable summary says why (issue #78 defect 1):
    printing an empty line left an LLM failure looking like a document
    that simply had nothing to say.

    Args:
        summary: The stored summary text, or ``None``/empty if none exists.
        status: The document's ``summary_status`` — ``'ok'``, ``'failed'``
            or ``'pending'``.

    Returns:
        The truncated summary text, or a status-specific placeholder when
        no summary text is available.
    """
    text = (summary or "").strip()
    if text:
        if len(text) > SUMMARY_SNIPPET_CHARS:
            return text[:SUMMARY_SNIPPET_CHARS] + "..."
        return text
    if status == SUMMARY_STATUS_FAILED:
        return "(summary unavailable — generation failed)"
    return "(no summary)"


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------

async def _doc_add_handler(
    arguments: dict, context: ToolContext,
    db, embed_client, app,
) -> ToolResult:
    """Add a document by URL or inline text."""
    url = arguments.get("url", "").strip()
    text = arguments.get("text", "").strip()
    title = arguments.get("title", "").strip() or None
    filename = arguments.get("filename", "").strip() or None

    if not url and not text:
        return ToolResult(
            content="Provide either 'url' or 'text' to add a document.",
            is_error=True,
        )

    try:
        if url:
            content_bytes, auto_filename, mime_type = await fetch_url_content(url)
            filename = filename or auto_filename
            source_type = "url"
        else:
            content_bytes = text.encode("utf-8")
            filename = filename or "document.txt"
            mime_type = "text/plain"
            source_type = "text"

        result = await ingest_document(
            app=app, db=db, embed_client=embed_client,
            content_bytes=content_bytes,
            filename=filename,
            mime_type=mime_type,
            discussion_id=context.discussion_id,
            source_url=url or None,
            title=title,
            source_type=source_type,
            context=context,
        )

        if "error" in result:
            return ToolResult(content=result["error"], is_error=True)

        return ToolResult(
            content=json.dumps(result, indent=2),
            metadata=result,
        )
    except DocumentError as e:
        logger.warning("doc_add failed for %s: %s", url or filename, e)
        return ToolResult(content=f"Failed to add document: {e}", is_error=True)
    except Exception as e:
        logger.exception("doc_add failed unexpectedly for %s", url or filename)
        return ToolResult(content=f"Failed to add document: {e}", is_error=True)


async def _doc_list_handler(
    arguments: dict, context: ToolContext,
    db, embed_client, app,
) -> ToolResult:
    """List documents for the current discussion, or search full library."""
    full_library = arguments.get("full_library", False)
    query = arguments.get("query", "").strip()

    if full_library and query:
        # Semantic search across all documents
        try:
            query_vec = await embed_client.embed(query)
        except Exception as e:
            return ToolResult(
                content=f"Embedding service unavailable: {e}", is_error=True,
            )

        rows = db.get_all_chunks_with_embeddings()
        if not rows:
            return ToolResult(content="No documents in the library yet.")

        ranking = _rank_by_similarity(
            query_vec, rows, limit=LIBRARY_SEARCH_LIMIT,
            threshold=MIN_SIMILARITY_THRESHOLD,
        )
        scored = ranking.ranked

        # Group by document
        seen_docs: dict[int, dict] = {}
        for score, row in scored:
            doc_id = row["document_id"]
            if doc_id not in seen_docs:
                doc = db.get_document(doc_id)
                if doc:
                    seen_docs[doc_id] = {
                        "id": doc_id,
                        "title": doc["title"],
                        "summary": doc["summary"],
                        "summary_status": doc.get(
                            "summary_status", SUMMARY_STATUS_OK,
                        ),
                        "filename": doc["filename"],
                        "char_count": doc["char_count"],
                        "best_score": score,
                    }

        if not seen_docs:
            if ranking.skipped_dim_mismatch:
                return ToolResult(
                    content=_reindex_message(ranking), is_error=True)
            return ToolResult(content=f"No documents match '{query}'.")

        docs_list = sorted(
            seen_docs.values(), key=lambda d: d["best_score"], reverse=True,
        )
        lines = [f"Library search for '{query}' — {len(docs_list)} document(s):\n"]
        for doc in docs_list:
            summary_snippet = _summary_snippet(
                doc["summary"], doc.get("summary_status", SUMMARY_STATUS_OK),
            )
            lines.append(
                f"  [ID {doc['id']}] {doc['title']} ({doc['char_count']} chars, "
                f"score: {doc['best_score']:.2f})\n    {summary_snippet}"
            )
        return ToolResult(content="\n".join(lines), metadata={"count": len(docs_list)})

    elif full_library:
        # List all documents
        docs = db.get_all_documents()
        if not docs:
            return ToolResult(content="No documents in the library.")
        lines = [f"All documents in library — {len(docs)} total:\n"]
        for doc in docs:
            summary_snippet = _summary_snippet(
                doc["summary"], doc.get("summary_status", SUMMARY_STATUS_OK),
            )
            lines.append(
                f"  [ID {doc['id']}] {doc['title']} ({doc['char_count']} chars)\n"
                f"    {summary_snippet}"
            )
        return ToolResult(content="\n".join(lines), metadata={"count": len(docs)})

    else:
        # List documents for current discussion
        docs = db.get_discussion_documents(context.discussion_id)
        if not docs:
            return ToolResult(content="No documents attached to this discussion.")
        lines = [f"Documents in this discussion — {len(docs)} total:\n"]
        for doc in docs:
            summary_snippet = _summary_snippet(
                doc["summary"], doc.get("summary_status", SUMMARY_STATUS_OK),
            )
            lines.append(
                f"  [ID {doc['id']}] {doc['title']} ({doc['char_count']} chars)\n"
                f"    {summary_snippet}"
            )
        return ToolResult(content="\n".join(lines), metadata={"count": len(docs)})


async def _doc_get_length_handler(
    arguments: dict, context: ToolContext,
    db, embed_client, app,
) -> ToolResult:
    """Return the character count of a document."""
    doc_id = arguments.get("document_id")
    if doc_id is None:
        return ToolResult(content="document_id is required.", is_error=True)

    doc = db.get_document(int(doc_id))
    if not doc:
        return ToolResult(content=f"Document {doc_id} not found.", is_error=True)

    return ToolResult(
        content=json.dumps({"document_id": doc_id, "char_count": doc["char_count"]}),
        metadata={"char_count": doc["char_count"]},
    )


async def _doc_get_text_handler(
    arguments: dict, context: ToolContext,
    db, embed_client, app,
) -> ToolResult:
    """Return a slice of the document's markdown text."""
    doc_id = arguments.get("document_id")
    from_char = int(arguments.get("from_char", 0))
    to_char = int(arguments.get("to_char", -1))

    if doc_id is None:
        return ToolResult(content="document_id is required.", is_error=True)

    markdown = db.get_document_markdown(int(doc_id))
    if markdown is None:
        return ToolResult(content=f"Document {doc_id} not found.", is_error=True)

    try:
        from_char, to_char = resolve_range(from_char, to_char, len(markdown))
    except ValueError as e:
        return ToolResult(content=f"Invalid range: {e}", is_error=True)
    text = markdown[from_char:to_char]

    return ToolResult(
        content=text,
        metadata={"from_char": from_char, "to_char": to_char, "length": len(text)},
    )


async def _doc_get_sections_handler(
    arguments: dict, context: ToolContext,
    db, embed_client, app,
) -> ToolResult:
    """Return the list of section headers with character offsets."""
    doc_id = arguments.get("document_id")
    if doc_id is None:
        return ToolResult(content="document_id is required.", is_error=True)

    doc = db.get_document(int(doc_id))
    if not doc:
        return ToolResult(content=f"Document {doc_id} not found.", is_error=True)

    sections = json.loads(doc["sections_json"])
    if not sections:
        return ToolResult(content="No sections found in this document.")

    lines = [f"Sections in '{doc['title']}' ({len(sections)} total):\n"]
    for s in sections:
        indent = "  " * (s["level"] - 1)
        lines.append(
            f"{indent}{'#' * s['level']} {s['header']} "
            f"(chars {s['from_char']}-{s['to_char']})"
        )
    return ToolResult(
        content="\n".join(lines),
        metadata={"sections": sections},
    )


async def _doc_get_chapter_handler(
    arguments: dict, context: ToolContext,
    db, embed_client, app,
) -> ToolResult:
    """Return the text of a named section (fuzzy match on header)."""
    doc_id = arguments.get("document_id")
    header = arguments.get("header", "").strip()

    if doc_id is None:
        return ToolResult(content="document_id is required.", is_error=True)
    if not header:
        return ToolResult(content="header is required.", is_error=True)

    doc = db.get_document(int(doc_id))
    if not doc:
        return ToolResult(content=f"Document {doc_id} not found.", is_error=True)

    sections = json.loads(doc["sections_json"])
    if not sections:
        return ToolResult(content="No sections found in this document.", is_error=True)

    # Find best matching section (case-insensitive substring match)
    header_lower = header.lower()
    best_match = None
    best_score = 0
    for s in sections:
        s_lower = s["header"].lower()
        if s_lower == header_lower:
            best_match = s
            break
        elif header_lower in s_lower or s_lower in header_lower:
            score = len(header_lower) / max(len(s_lower), 1)
            if score > best_score:
                best_score = score
                best_match = s

    if not best_match:
        available = ", ".join(s["header"] for s in sections[:AVAILABLE_HEADERS_HINT])
        return ToolResult(
            content=f"No section matching '{header}'. Available: {available}",
            is_error=True,
        )

    # Get the section text
    markdown = db.get_document_markdown(int(doc_id))
    if markdown is None:
        return ToolResult(content="Could not read document text.", is_error=True)

    text = markdown[best_match["from_char"]:best_match["to_char"]]

    return ToolResult(
        content=text,
        metadata={
            "header": best_match["header"],
            "from_char": best_match["from_char"],
            "to_char": best_match["to_char"],
        },
    )
