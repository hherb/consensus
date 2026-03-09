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
"""

import asyncio
import json
import logging
import math
import re
import struct
import time
from typing import Optional

import httpx

from .ai_client import AIClient
from .models import AIConfig, resolve_api_key
from .tools import PythonToolProvider, ToolContext, ToolDefinition, ToolResult

logger = logging.getLogger(__name__)

# Chunking defaults
DEFAULT_CHUNK_SIZE = 1500  # characters
DEFAULT_CHUNK_OVERLAP = 200  # characters

# RAG defaults
RAG_TOP_K = 5
MIN_SIMILARITY_THRESHOLD = 0.3

# Timeout for URL fetching
URL_FETCH_TIMEOUT = 30.0

# Timeout for interpretation LLM calls
LLM_TIMEOUT = 120.0

# Max chars to send to LLM for summarization in a single call
SUMMARY_CHUNK_LIMIT = 4000


# ---------------------------------------------------------------------------
# Document parsing
# ---------------------------------------------------------------------------

def parse_document(content: bytes, filename: str, mime_type: str) -> str:
    """Convert document bytes to markdown text.

    Supported formats:
    - PDF: pdfplumber (preferred) or PyPDF2 fallback
    - HTML: trafilatura
    - Plain text / Markdown: pass through (decoded as UTF-8)
    """
    if mime_type == "application/pdf" or filename.lower().endswith(".pdf"):
        return _parse_pdf(content)
    elif mime_type in ("text/html", "application/xhtml+xml") or \
            filename.lower().endswith((".html", ".htm")):
        return _parse_html(content)
    else:
        # Plain text or markdown — decode and return
        return content.decode("utf-8", errors="replace")


def _parse_pdf(content: bytes) -> str:
    """Extract text from PDF bytes."""
    try:
        import pdfplumber
        import io
        pages = []
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            for i, page in enumerate(pdf.pages):
                text = page.extract_text() or ""
                if text.strip():
                    pages.append(f"## Page {i + 1}\n\n{text}")
        if pages:
            return "\n\n".join(pages)
    except ImportError:
        logger.info("pdfplumber not available, trying PyPDF2")
    except Exception as e:
        logger.warning("pdfplumber failed: %s, trying PyPDF2", e)

    try:
        from PyPDF2 import PdfReader
        import io
        reader = PdfReader(io.BytesIO(content))
        pages = []
        for i, page in enumerate(reader.pages):
            text = page.extract_text() or ""
            if text.strip():
                pages.append(f"## Page {i + 1}\n\n{text}")
        return "\n\n".join(pages) if pages else "(Empty PDF)"
    except ImportError:
        raise ImportError(
            "PDF parsing requires pdfplumber or PyPDF2. "
            "Install with: uv pip install pdfplumber"
        )


def _parse_html(content: bytes) -> str:
    """Extract readable text from HTML bytes using trafilatura."""
    try:
        import trafilatura
        text = trafilatura.extract(
            content.decode("utf-8", errors="replace"),
            include_comments=False,
            include_tables=True,
        )
        if text:
            return text
    except Exception as e:
        logger.warning("trafilatura failed: %s", e)

    # Fallback: strip HTML tags
    html_text = content.decode("utf-8", errors="replace")
    return re.sub(r"<[^>]+>", "", html_text).strip()


async def fetch_url_content(url: str) -> tuple[bytes, str, str]:
    """Fetch content from a URL. Returns (content_bytes, filename, mime_type)."""
    async with httpx.AsyncClient(
        timeout=URL_FETCH_TIMEOUT, follow_redirects=True
    ) as client:
        response = await client.get(url)
        response.raise_for_status()
        content_type = response.headers.get("content-type", "text/html")
        mime_type = content_type.split(";")[0].strip()
        # Derive filename from URL
        from urllib.parse import urlparse
        path = urlparse(url).path
        filename = path.split("/")[-1] or "document"
        if not filename.endswith((".pdf", ".html", ".htm", ".txt", ".md")):
            if "pdf" in mime_type:
                filename += ".pdf"
            elif "html" in mime_type:
                filename += ".html"
        return response.content, filename, mime_type


# ---------------------------------------------------------------------------
# Section extraction
# ---------------------------------------------------------------------------

_HEADER_RE = re.compile(r"^(#{1,4})\s+(.+)$", re.MULTILINE)


def extract_sections(markdown: str) -> list[dict]:
    """Extract markdown headers with character offsets.

    Returns [{header, level, from_char, to_char}, ...]
    where from_char is the start of the section and to_char is the start
    of the next section (or end of document).
    """
    sections = []
    for match in _HEADER_RE.finditer(markdown):
        level = len(match.group(1))
        header = match.group(2).strip()
        from_char = match.start()
        sections.append({
            "header": header,
            "level": level,
            "from_char": from_char,
            "to_char": len(markdown),  # will be updated below
        })

    # Fix to_char: each section ends where the next one begins
    for i in range(len(sections) - 1):
        sections[i]["to_char"] = sections[i + 1]["from_char"]

    return sections


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------

def chunk_document(
    markdown: str,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> list[dict]:
    """Split markdown into overlapping chunks, respecting paragraph boundaries.

    Returns [{chunk_index, content, from_char, to_char, section_header}, ...]
    """
    if not markdown.strip():
        return []

    sections = extract_sections(markdown)
    paragraphs = _split_paragraphs(markdown)
    chunks = []
    current_content = []
    current_start = 0
    current_length = 0
    chunk_index = 0

    for para_start, para_text in paragraphs:
        para_len = len(para_text)

        if current_length + para_len > chunk_size and current_content:
            # Emit current chunk
            chunk_text = "\n\n".join(current_content)
            chunk_end = para_start
            section_header = _find_section_for_offset(current_start, sections)
            chunks.append({
                "chunk_index": chunk_index,
                "content": chunk_text,
                "from_char": current_start,
                "to_char": chunk_end,
                "section_header": section_header,
            })
            chunk_index += 1

            # Start new chunk with overlap
            overlap_text = chunk_text[-overlap:] if overlap > 0 else ""
            if overlap_text:
                current_content = [overlap_text]
                current_start = chunk_end - len(overlap_text)
                current_length = len(overlap_text)
            else:
                current_content = []
                current_start = para_start
                current_length = 0

        current_content.append(para_text)
        current_length += para_len

    # Emit final chunk
    if current_content:
        chunk_text = "\n\n".join(current_content)
        section_header = _find_section_for_offset(current_start, sections)
        chunks.append({
            "chunk_index": chunk_index,
            "content": chunk_text,
            "from_char": current_start,
            "to_char": len(markdown),
            "section_header": section_header,
        })

    return chunks


def _split_paragraphs(text: str) -> list[tuple[int, str]]:
    """Split text into (offset, paragraph_text) pairs on double newlines."""
    result = []
    pos = 0
    for part in re.split(r"\n\n+", text):
        stripped = part.strip()
        if stripped:
            idx = text.find(part, pos)
            result.append((idx, stripped))
            pos = idx + len(part)
    return result


def _find_section_for_offset(offset: int, sections: list[dict]) -> Optional[str]:
    """Find the section header that contains the given character offset."""
    for section in reversed(sections):
        if offset >= section["from_char"]:
            return section["header"]
    return None


# ---------------------------------------------------------------------------
# Embedding helpers (reuse patterns from tools_memory.py)
# ---------------------------------------------------------------------------

def _pack_embedding(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


def _unpack_embedding(blob: bytes) -> list[float]:
    n = len(blob) // 4
    return list(struct.unpack(f"{n}f", blob))


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(x * x for x in b))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)


def _rank_by_similarity(
    query_vec: list[float], rows: list[dict], limit: int,
    threshold: float = 0.0,
) -> list[tuple[float, dict]]:
    """Sort rows by cosine similarity, return top-limit above threshold."""
    scored = []
    for row in rows:
        emb = _unpack_embedding(row["embedding"])
        score = _cosine_similarity(query_vec, emb)
        if score >= threshold:
            scored.append((score, row))
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[:limit]


# ---------------------------------------------------------------------------
# Tool schemas
# ---------------------------------------------------------------------------

_DOC_ADD_SCHEMA = {
    "type": "object",
    "properties": {
        "url": {
            "type": "string",
            "description": "URL to fetch and add as a document.",
        },
        "text": {
            "type": "string",
            "description": "Inline text content to add as a document.",
        },
        "title": {
            "type": "string",
            "description": "Title for the document (auto-detected if not provided).",
        },
        "filename": {
            "type": "string",
            "description": "Filename for the document (auto-detected if not provided).",
        },
    },
}

_DOC_LIST_SCHEMA = {
    "type": "object",
    "properties": {
        "full_library": {
            "type": "boolean",
            "description": (
                "If true, search all documents in the database (not just this discussion). "
                "Requires 'query' parameter for semantic search."
            ),
            "default": False,
        },
        "query": {
            "type": "string",
            "description": "Search query for full_library mode. Finds documents by semantic similarity.",
        },
    },
}

_DOC_LENGTH_SCHEMA = {
    "type": "object",
    "properties": {
        "document_id": {
            "type": "integer",
            "description": "ID of the document to query.",
        },
    },
    "required": ["document_id"],
}

_DOC_TEXT_SCHEMA = {
    "type": "object",
    "properties": {
        "document_id": {
            "type": "integer",
            "description": "ID of the document.",
        },
        "from_char": {
            "type": "integer",
            "description": "Start character offset (inclusive, 0-based).",
        },
        "to_char": {
            "type": "integer",
            "description": "End character offset (exclusive, -1 for end of document).",
        },
    },
    "required": ["document_id", "from_char", "to_char"],
}

_DOC_SECTIONS_SCHEMA = {
    "type": "object",
    "properties": {
        "document_id": {
            "type": "integer",
            "description": "ID of the document.",
        },
    },
    "required": ["document_id"],
}

_DOC_CHAPTER_SCHEMA = {
    "type": "object",
    "properties": {
        "document_id": {
            "type": "integer",
            "description": "ID of the document.",
        },
        "header": {
            "type": "string",
            "description": "Section header text to retrieve (case-insensitive match).",
        },
    },
    "required": ["document_id", "header"],
}

_DOC_ASK_SCHEMA = {
    "type": "object",
    "properties": {
        "document_id": {
            "type": "integer",
            "description": "ID of the document to query.",
        },
        "question": {
            "type": "string",
            "description": "Question to ask about the document.",
        },
    },
    "required": ["document_id", "question"],
}

_DOC_SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "document_id": {
            "type": "integer",
            "description": "ID of the document.",
        },
        "from_char": {
            "type": "integer",
            "description": "Start character offset (default 0).",
            "default": 0,
        },
        "to_char": {
            "type": "integer",
            "description": "End character offset (default -1 = end of document).",
            "default": -1,
        },
    },
    "required": ["document_id"],
}


# ---------------------------------------------------------------------------
# Background embedding task
# ---------------------------------------------------------------------------

_embedding_docs: set[int] = set()  # track documents currently being embedded


async def _embed_document_chunks(doc_id: int, db, embed_client) -> None:
    """Background task: embed all unembedded chunks for a document."""
    try:
        chunks = db.get_document_chunks(doc_id)
        for chunk in chunks:
            try:
                # Check if already embedded
                existing = db.get_chunks_with_embeddings(doc_id)
                embedded_ids = {c["id"] for c in existing}
                if chunk["id"] in embedded_ids:
                    continue
                vec = await embed_client.embed(chunk["content"][:1000])
                blob = _pack_embedding(vec)
                db.set_chunk_embedding(chunk["id"], blob)
            except Exception as e:
                logger.warning(
                    "Failed to embed chunk %d of doc %d: %s",
                    chunk["id"], doc_id, e,
                )
                break  # Stop if embedding service is down
    finally:
        _embedding_docs.discard(doc_id)


# ---------------------------------------------------------------------------
# LLM helper for interpretation
# ---------------------------------------------------------------------------

async def _call_interpretation_llm(
    app, context: ToolContext,
    system_prompt: str, user_prompt: str,
) -> str:
    """Call an LLM for document interpretation using the caller entity's config."""
    entity = app.db.get_entity(context.caller_entity_id)
    if not entity:
        return "(Error: could not resolve caller entity for LLM call)"

    ai_config = AIConfig.from_db_row(entity)
    # Resolve API key via app's key resolver
    api_key = app._resolve_key_for_moderator(
        ai_config.provider_id, entity.get("api_key_env", ""),
    )

    client = AIClient(base_url=ai_config.base_url, api_key=api_key)
    try:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        response = await client.complete(
            messages=messages,
            model=ai_config.model,
            temperature=0.3,
            max_tokens=ai_config.max_tokens,
        )
        return response.content
    except Exception as e:
        logger.warning("Interpretation LLM call failed: %s", e)
        return f"(LLM call failed: {e})"
    finally:
        await client.close()


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
    """Parse, chunk, store, and embed a document.

    Returns document metadata dict.
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
            # Use first 3000 chars for summary generation
            excerpt = markdown[:3000]
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
        asyncio.create_task(_embed_document_chunks(doc_id, db, embed_client))

    return {
        "document_id": doc_id,
        "title": title,
        "summary": summary,
        "char_count": char_count,
        "filename": filename,
        "sections": len(sections),
        "chunks": len(chunks),
    }


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
    except Exception as e:
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
            query_vec, rows, limit=20, threshold=MIN_SIMILARITY_THRESHOLD,
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
            summary_snippet = (doc["summary"][:150] + "...") if len(doc["summary"]) > 150 else doc["summary"]
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
            summary_snippet = (doc["summary"][:150] + "...") if len(doc["summary"]) > 150 else doc["summary"]
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
            summary_snippet = (doc["summary"][:150] + "...") if len(doc["summary"]) > 150 else doc["summary"]
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
        available = ", ".join(s["header"] for s in sections[:10])
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
            {"text": p["text"][:500], "from_char": p["from_char"], "to_char": p["to_char"]}
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
    from .tools_memory import EmbeddingClient
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
                "Get the full text of a named section/chapter. "
                "Uses fuzzy matching on the header text."
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
