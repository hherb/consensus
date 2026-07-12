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

    # Try splitting on structured headers.  The header's own number
    # determines attribution — a participant who skips or reorders
    # sub-questions must not shift every analysis to the wrong slot.
    pattern = r'\*\*(?:Sub-question\s+|Q)(\d+)[.:]\*?\*?\s*'
    alt_pattern = r'\*\*(\d+)\.\*\*\s*'

    sections = _sections_by_header_number(content, pattern)
    if not sections:
        sections = _sections_by_header_number(content, alt_pattern)

    if sections:
        result: dict[int, str] = {}
        for idx in range(num_subquestions):
            result[idx] = sections.get(idx, "")
        return result

    # Fallback: associate entire content with all sub-questions
    stripped = content.strip()
    return {idx: stripped for idx in range(num_subquestions)}


def _sections_by_header_number(content: str,
                               pattern: str) -> dict[int, str]:
    """Map 0-based sub-question index to the text after its header.

    The index comes from the number captured in the header itself
    (1-based in the text), not from the header's position.
    """
    matches = list(re.finditer(pattern, content, flags=re.IGNORECASE))
    sections: dict[int, str] = {}
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
        idx = int(m.group(1)) - 1
        if idx >= 0:
            sections[idx] = content[m.end():end].strip()
    return sections
