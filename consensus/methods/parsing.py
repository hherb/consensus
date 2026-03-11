"""Shared parsing utilities for discussion method phase handlers.

Pure functions for extracting structured data from AI-generated text.
Used across multiple methods to avoid duplication.
"""

from __future__ import annotations

import json
import re
from typing import Optional, Union


def extract_json_block(content: str) -> Optional[Union[dict, list]]:
    """Extract the first JSON object or array from a fenced code block.

    Handles both ```json and ``` fences.  Returns None if no valid
    JSON block is found.
    """
    match = re.search(r'```(?:json)?\s*(\{[\s\S]*?\}|\[[\s\S]*?\])\s*```',
                      content, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except (json.JSONDecodeError, ValueError):
        return None


def parse_numbered_list(content: str, min_length: int = 10) -> list[str]:
    """Extract items from a numbered, prefixed, or bulleted list.

    Tries multiple patterns in priority order:
      1. Dot/paren numbered: ``1. item`` or ``1) item``
      2. Prefixed: ``A1: item`` or ``H2) item``
      3. Bulleted: ``- item`` or ``* item``

    Items shorter than *min_length* characters are filtered out.
    Trailing periods are stripped.
    """
    patterns = [
        r'^\s*\d+[\.\)]\s*(.+)',
        r'^\s*[A-Z]\d+[\.\):]\s*(.+)',
        r'^\s*[-*]\s+(.+)',
    ]
    for pattern in patterns:
        matches = re.findall(pattern, content, re.MULTILINE)
        if matches:
            return [m.strip().rstrip('.')
                    for m in matches
                    if len(m.strip()) >= min_length]
    return []


def word_overlap_similar(a: str, b: str, threshold: float = 0.7) -> bool:
    """Check if two strings are substantially similar by word overlap.

    Returns True if the Jaccard-like overlap ratio exceeds *threshold*.
    """
    w1 = set(a.lower().split())
    w2 = set(b.lower().split())
    if not w1 or not w2:
        return False
    overlap = len(w1 & w2) / max(len(w1), len(w2))
    return overlap > threshold
