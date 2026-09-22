"""The document ingestion pipeline: parse, chunk, store, embed."""

import json
import logging
from typing import Optional

from ..tools import ToolContext
from .chunking import chunk_document
from .constants import SUMMARY_EXCERPT_CHARS
from .embedding import _embed_document_chunks, _embedding_docs, _spawn_background
from .llm import _call_interpretation_llm
from .parsing import extract_sections, parse_document

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Document ingestion pipeline
# ---------------------------------------------------------------------------

async def ingest_document(
    app, db, embed_client,
    content_bytes: bytes,
    filename: str,
    mime_type: str,
    discussion_id: Optional[int] = None,
    source_url: Optional[str] = None,
    title: Optional[str] = None,
    source_type: str = "upload",
    generate_summary: bool = True,
    context: Optional[ToolContext] = None,
) -> dict:
    """Parse, chunk and store a document, then *schedule* its embedding.

    Returns a document metadata dict, or ``{"error": ...}`` if parsing yielded
    no text. Note three things the signature does not show:

    - Embedding is fire-and-forget: on return the chunks are stored but not
      yet embedded, which is why ``_doc_ask_handler`` has a "still being
      indexed" branch. The document id is added to the module-level
      ``_embedding_docs`` marker set for the duration of that pass.
    - ``generate_summary`` is silently a no-op unless both ``context`` and
      ``app`` are supplied; without them the document is stored with an empty
      summary and no error is reported.
    - The document is associated with ``discussion_id`` when one is given.
    """
    # Parse to markdown
    markdown = parse_document(content_bytes, filename, mime_type)
    if not markdown.strip():
        return {"error": "Document is empty after parsing."}

    char_count = len(markdown)

    # Extract sections
    sections = extract_sections(markdown)
    sections_json = json.dumps(sections)

    # Auto-detect title from first header if not provided
    if not title:
        if sections:
            title = sections[0]["header"]
        else:
            title = filename

    # Generate summary
    summary = ""
    if generate_summary and context and app:
        try:
            excerpt = markdown[:SUMMARY_EXCERPT_CHARS]
            summary = await _call_interpretation_llm(
                app, context,
                system_prompt=(
                    "You are a document analyst. Provide a brief summary "
                    "(2-3 sentences) of the following document excerpt. "
                    "Focus on the main topic, key findings or arguments."
                ),
                user_prompt=excerpt,
            )
        except Exception as e:
            logger.warning("Summary generation failed: %s", e)
            summary = ""

    # Store document
    doc_id = db.add_document(
        filename=filename,
        title=title,
        summary=summary,
        mime_type=mime_type,
        source_type=source_type,
        source_url=source_url,
        markdown=markdown,
        char_count=char_count,
        sections_json=sections_json,
    )

    # Associate with discussion
    if discussion_id:
        db.add_discussion_document(discussion_id, doc_id)

    # Chunk the document
    chunks = chunk_document(markdown)
    for chunk in chunks:
        db.add_document_chunk(
            document_id=doc_id,
            chunk_index=chunk["chunk_index"],
            content=chunk["content"],
            from_char=chunk["from_char"],
            to_char=chunk["to_char"],
            section_header=chunk.get("section_header"),
        )

    # Background embedding
    if embed_client and doc_id not in _embedding_docs:
        _embedding_docs.add(doc_id)
        _spawn_background(_embed_document_chunks(doc_id, db, embed_client))

    return {
        "document_id": doc_id,
        "title": title,
        "summary": summary,
        "char_count": char_count,
        "filename": filename,
        "sections": len(sections),
        "chunks": len(chunks),
    }
