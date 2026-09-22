"""Paragraph-aware chunking of a markdown document."""

import re
from typing import Optional

from .constants import DEFAULT_CHUNK_OVERLAP, DEFAULT_CHUNK_SIZE
from .parsing import extract_sections


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
