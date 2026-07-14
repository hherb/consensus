"""Shared parsing utilities for discussion method phase handlers.

Pure functions for extracting structured data from AI-generated text.
Used across multiple methods to avoid duplication.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Optional, Union

logger = logging.getLogger(__name__)


def extract_json_block(content: str) -> Optional[Union[dict, list]]:
    """Extract the first JSON object or array from a fenced code block.

    Handles both ```json and ``` fences.  If the response contains
    multiple JSON blocks, only the first is returned.  Returns None if
    no valid JSON block is found.
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


def _balanced_object_end(content: str, start: int) -> Optional[int]:
    """Index of the ``}`` closing the object opened at ``start``, or None.

    Counts brace depth character-by-character; braces inside JSON
    strings are not excluded (a known, accepted limitation shared with
    the fenced-block path — such payloads fail the subsequent parse
    and the next candidate is tried).
    """
    depth = 0
    for pos in range(start, len(content)):
        if content[pos] == "{":
            depth += 1
        elif content[pos] == "}":
            depth -= 1
            if depth == 0:
                return pos
    return None


def extract_json_payload(content: str,
                         key: str) -> Optional[Union[dict, list]]:
    """Extract the value of ``key`` from JSON embedded in free text.

    Tries a fenced JSON block first, then any inline (unfenced) object
    containing ``"key"``: every candidate ``{`` before the key is
    tried nearest-first and scanned to its balanced closing brace — a
    lazy regex would truncate at the first inner brace, and requiring
    the key in first position would miss pretty-printed or reordered
    objects.  Returns the value only when it is a dict or list.
    Candidates that fail to parse are logged and the next is tried;
    callers own the user-facing warning when nothing is extracted.
    """
    data = extract_json_block(content)
    if isinstance(data, dict) and isinstance(data.get(key), (dict, list)):
        return data[key]
    marker = content.find(f'"{key}"')
    if marker == -1:
        return None
    starts = [pos for pos, char in enumerate(content[:marker])
              if char == "{"]
    for start in reversed(starts):
        end = _balanced_object_end(content, start)
        if end is None or end < marker:
            continue  # candidate closes before the key — not enclosing
        try:
            data = json.loads(content[start:end + 1])
        except (json.JSONDecodeError, ValueError) as exc:
            logger.debug(
                "Inline JSON candidate at offset %d failed to parse: %s",
                start, exc)
            continue
        if isinstance(data, dict) and isinstance(data.get(key),
                                                 (dict, list)):
            return data[key]
    return None
