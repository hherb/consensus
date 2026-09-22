"""Handlers behind the eight ``doc_*`` tools."""

import json
import logging
from typing import Optional

from ..tools import ToolContext, ToolResult
from .constants import (
    AVAILABLE_HEADERS_HINT, LIBRARY_SEARCH_LIMIT, MIN_SIMILARITY_THRESHOLD,
    PASSAGE_PREVIEW_CHARS, RAG_TOP_K, SUMMARY_CHUNK_LIMIT,
    SUMMARY_SNIPPET_CHARS,
)
from .embedding import _embedding_docs, _rank_by_similarity, _spawn_embedding_pass
from .errors import DocumentError
from .ingestion import ingest_document
from .llm import _call_interpretation_llm
from .parsing import fetch_url_content

logger = logging.getLogger(__name__)


def _summary_snippet(summary: Optional[str]) -> str:
    """Truncate a document summary for a one-line ``doc_list`` entry."""
    text = summary or ""
    if len(text) > SUMMARY_SNIPPET_CHARS:
        return text[:SUMMARY_SNIPPET_CHARS] + "..."
    return text


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

        scored = _rank_by_similarity(
            query_vec, rows, limit=LIBRARY_SEARCH_LIMIT,
            threshold=MIN_SIMILARITY_THRESHOLD,
        )

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
                        "filename": doc["filename"],
                        "char_count": doc["char_count"],
                        "best_score": score,
                    }

        if not seen_docs:
            return ToolResult(content=f"No documents match '{query}'.")

        docs_list = sorted(
            seen_docs.values(), key=lambda d: d["best_score"], reverse=True,
        )
        lines = [f"Library search for '{query}' — {len(docs_list)} document(s):\n"]
        for doc in docs_list:
            summary_snippet = _summary_snippet(doc["summary"])
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
            summary_snippet = _summary_snippet(doc["summary"])
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
            summary_snippet = _summary_snippet(doc["summary"])
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

    if to_char == -1:
        to_char = len(markdown)
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


async def _doc_ask_handler(
    arguments: dict, context: ToolContext,
    db, embed_client, app,
) -> ToolResult:
    """RAG pipeline: embed question, retrieve top-k chunks, call LLM."""
    doc_id = arguments.get("document_id")
    question = arguments.get("question", "").strip()

    if doc_id is None:
        return ToolResult(content="document_id is required.", is_error=True)
    if not question:
        return ToolResult(content="question is required.", is_error=True)

    doc_id = int(doc_id)
    doc = db.get_document(doc_id)
    if not doc:
        return ToolResult(content=f"Document {doc_id} not found.", is_error=True)

    # Check if embeddings are ready
    unembedded = db.count_unembedded_chunks(doc_id)
    if unembedded > 0:
        # Re-kick the background embedding pass if it is not already running,
        # so a previously failed/interrupted chunk is retried instead of
        # leaving the document permanently stuck as "still being indexed".
        if embed_client and doc_id not in _embedding_docs:
            _embedding_docs.add(doc_id)
            _spawn_embedding_pass(doc_id, db, embed_client)
        total_chunks = len(db.get_document_chunks(doc_id))
        embedded = total_chunks - unembedded
        return ToolResult(
            content=(
                f"Document is still being indexed ({embedded}/{total_chunks} chunks embedded). "
                "Please try again shortly."
            ),
        )

    # Embed the question
    try:
        query_vec = await embed_client.embed(question)
    except Exception as e:
        return ToolResult(
            content=f"Embedding service unavailable: {e}", is_error=True,
        )

    # Retrieve and rank chunks
    rows = db.get_chunks_with_embeddings(doc_id)
    if not rows:
        return ToolResult(content="No embedded chunks found for this document.")

    scored = _rank_by_similarity(query_vec, rows, RAG_TOP_K)

    # Build context for LLM
    passages = []
    for i, (score, row) in enumerate(scored, 1):
        passages.append({
            "index": i,
            "text": row["content"],
            "from_char": row["from_char"],
            "to_char": row["to_char"],
            "score": round(score, 3),
        })

    passages_text = "\n\n".join(
        f"[Passage {p['index']}] (chars {p['from_char']}-{p['to_char']}, "
        f"relevance: {p['score']}):\n{p['text']}"
        for p in passages
    )

    answer = await _call_interpretation_llm(
        app, context,
        system_prompt=(
            "You are a document analyst. Answer the question based ONLY on the "
            "provided passages from the document. Cite passage numbers in your answer. "
            "If the answer is not in the passages, say so clearly."
        ),
        user_prompt=(
            f"DOCUMENT: {doc['title']}\n\n"
            f"PASSAGES:\n{passages_text}\n\n"
            f"QUESTION: {question}"
        ),
    )

    result = {
        "answer": answer,
        "relevant_passages": [
            {
                "text": p["text"][:PASSAGE_PREVIEW_CHARS],
                "from_char": p["from_char"], "to_char": p["to_char"],
            }
            for p in passages
        ],
    }
    return ToolResult(
        content=json.dumps(result, indent=2),
        metadata=result,
    )


async def _doc_summary_handler(
    arguments: dict, context: ToolContext,
    db, embed_client, app,
) -> ToolResult:
    """Summarize a document or a range of it."""
    doc_id = arguments.get("document_id")
    from_char = int(arguments.get("from_char", 0))
    to_char = int(arguments.get("to_char", -1))

    if doc_id is None:
        return ToolResult(content="document_id is required.", is_error=True)

    markdown = db.get_document_markdown(int(doc_id))
    if markdown is None:
        return ToolResult(content=f"Document {doc_id} not found.", is_error=True)

    if to_char == -1:
        to_char = len(markdown)
    text = markdown[from_char:to_char]

    if not text.strip():
        return ToolResult(content="Selected range is empty.")

    if len(text) <= SUMMARY_CHUNK_LIMIT:
        # Direct summarization
        summary = await _call_interpretation_llm(
            app, context,
            system_prompt=(
                "You are a document analyst. Provide a clear, comprehensive summary "
                "of the following text. Include key findings, methods, and conclusions."
            ),
            user_prompt=text,
        )
    else:
        # Map-reduce: summarize chunks, then summarize summaries
        chunk_summaries = []
        for i in range(0, len(text), SUMMARY_CHUNK_LIMIT):
            chunk = text[i:i + SUMMARY_CHUNK_LIMIT]
            chunk_summary = await _call_interpretation_llm(
                app, context,
                system_prompt=(
                    "Provide a concise summary of this text excerpt. "
                    "Focus on key points and findings."
                ),
                user_prompt=chunk,
            )
            chunk_summaries.append(chunk_summary)

        # Combine
        combined = "\n\n---\n\n".join(
            f"Section {i+1}:\n{s}" for i, s in enumerate(chunk_summaries)
        )
        summary = await _call_interpretation_llm(
            app, context,
            system_prompt=(
                "You are a document analyst. Synthesize these section summaries "
                "into a single coherent summary. Include all key findings, methods, "
                "and conclusions."
            ),
            user_prompt=combined,
        )

    return ToolResult(
        content=json.dumps({"summary": summary}),
        metadata={"summary": summary, "from_char": from_char, "to_char": to_char},
    )
