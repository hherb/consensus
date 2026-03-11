"""Helper utilities for Recursive Decomposition method.

Parsing functions for extracting per-sub-question analyses from
participant responses.
"""

from __future__ import annotations

import re


def extract_subquestion_analyses(content: str, num_subquestions: int) -> dict[int, str]:
    """Extract per-sub-question analyses from a structured response.

    Splits on headers matching patterns like:
      - ``**Sub-question 1:**``
      - ``**Q1:**``
      - ``**1.**``

    Returns a mapping of sub-question index (0-based) to analysis text.
    If no structured headers are detected, falls back to associating
    the entire content with every sub-question index.

    Args:
        content: The participant's full response text.
        num_subquestions: Expected number of sub-questions.

    Returns:
        Dict mapping 0-based sub-question index to analysis text.
    """
    if num_subquestions <= 0:
        return {}

    # Try splitting on structured headers
    pattern = r'\*\*(?:Sub-question\s+|Q)\d+[.:]\*?\*?\s*'
    alt_pattern = r'\*\*\d+\.\*\*\s*'

    sections = _split_on_headers(content, pattern)
    if not sections:
        sections = _split_on_headers(content, alt_pattern)

    if sections:
        result: dict[int, str] = {}
        for idx in range(num_subquestions):
            if idx < len(sections):
                result[idx] = sections[idx].strip()
            else:
                result[idx] = ""
        return result

    # Fallback: associate entire content with all sub-questions
    stripped = content.strip()
    return {idx: stripped for idx in range(num_subquestions)}


def _split_on_headers(content: str, pattern: str) -> list[str]:
    """Split content on header patterns, returning the text after each."""
    parts = re.split(pattern, content, flags=re.IGNORECASE)
    # First element is text before the first header (preamble) — skip it
    # Preserve all sections including blank ones to maintain index alignment
    return parts[1:] if len(parts) > 1 else []
