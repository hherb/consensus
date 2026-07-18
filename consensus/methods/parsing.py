"""Shared parsing utilities for discussion method phase handlers.

Pure functions for extracting structured data from AI-generated text.
Used across multiple methods to avoid duplication.
"""

from __future__ import annotations

import json
import logging
import re
from fractions import Fraction
from typing import Any, Callable, Optional, Union

logger = logging.getLogger(__name__)


def coerce_str(payload: dict[str, Any], key: str, default: str = "") -> str:
    """Return ``payload[key]`` as a stripped string, treating null and absence alike.

    ``str(payload.get(key, default)).strip()`` has a latent bug: a JSON ``null``
    value is returned verbatim by :meth:`dict.get` (the key *is* present), so it
    becomes the literal string ``"None"`` instead of the intended *default*.
    This helper coerces both a missing key and an explicit ``None`` value to
    *default*, then returns the stripped string form.
    """
    value = payload.get(key)
    if value is None:
        value = default
    return str(value).strip()


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


def _overlap_fraction(a: str, b: str) -> Fraction:
    """Word-overlap ratio of two strings as an exact rational.

    Tokens are whitespace-split and lowercased; the ratio is the shared
    token count over the larger token-set size, ``0`` when either string
    has no tokens.  Exact ``Fraction`` arithmetic keeps sums of ratios
    (``canonical_index`` centrality) independent of summation order,
    which float rounding cannot guarantee.
    """
    w1 = set(a.lower().split())
    w2 = set(b.lower().split())
    if not w1 or not w2:
        return Fraction(0)
    return Fraction(len(w1 & w2), max(len(w1), len(w2)))


def word_overlap_ratio(a: str, b: str) -> float:
    """Fraction of the larger token set shared by two strings (0.0-1.0).

    Tokens are whitespace-split and lowercased.  Returns 0.0 when either
    string has no tokens.  This is the continuous form behind
    ``word_overlap_similar``; ``canonical_index`` uses the exact
    ``_overlap_fraction`` underneath it.
    """
    return float(_overlap_fraction(a, b))


def word_overlap_similar(a: str, b: str, threshold: float = 0.7) -> bool:
    """Check if two strings are substantially similar by word overlap.

    Returns True if the Jaccard-like overlap ratio exceeds *threshold*.
    """
    return word_overlap_ratio(a, b) > threshold


def cluster_by_similarity(members: list,
                          text_of: Callable[[Any], str],
                          threshold: float = 0.7) -> list[list]:
    """Group *members* into clusters by word-overlap similarity.

    Two members share a cluster when their texts (via *text_of*) are
    ``word_overlap_similar`` at *threshold*; clusters are the connected
    components of that graph.  Grouping is **order-independent** — it
    depends only on the set of members and the symmetric similarity
    relation, not their order — and **transitive**: ``A~B`` and ``B~C``
    place A, B and C together even when A and C are not directly similar
    (this is the deterministic price of order-independence).  Clusters
    are returned ordered by the smallest original index they contain;
    members keep their original order within a cluster.
    """
    n = len(members)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:  # attach larger root to smaller -> root is the min index
            parent[max(ri, rj)] = min(ri, rj)

    texts = [text_of(m) for m in members]
    for i in range(n):
        for j in range(i + 1, n):
            if word_overlap_similar(texts[i], texts[j], threshold):
                union(i, j)

    groups: dict[int, list] = {}
    for idx, member in enumerate(members):
        groups.setdefault(find(idx), []).append(member)
    # dict insertion order == ascending component-min index (the min member
    # is the first of its component reached), so this is deterministic.
    return list(groups.values())


def canonical_index(members: list,
                    text_of: Callable[[Any], str]) -> int:
    """Index of the medoid of *members* — the most representative one.

    The medoid maximises total word-overlap ratio to the other members
    (the phrasing most central to the group).  Centrality is summed in
    exact rational arithmetic (``_overlap_fraction``), so genuine ties
    stay ties regardless of member order and are broken toward the
    longest text, then the lexicographically smallest — the chosen text
    is fully deterministic and permutation-independent.  *members* must
    be non-empty.
    """
    if not members:
        raise ValueError("canonical_index requires a non-empty members list")
    texts = [text_of(m) for m in members]
    central = [sum(_overlap_fraction(texts[i], texts[j])
                   for j in range(len(texts)) if j != i)
               for i in range(len(texts))]
    best = 0
    for i in range(1, len(texts)):
        key_i = (central[i], len(texts[i]))
        key_best = (central[best], len(texts[best]))
        if key_i > key_best or (key_i == key_best
                                and texts[i] < texts[best]):
            best = i
    return best


def cluster_groups(raw: list[dict], text_key: str = "text",
                   threshold: float = 0.7) -> list[tuple[list[int], int]]:
    """Cluster *raw* contribution dicts into index groups with a medoid.

    Returns one ``(members, canon)`` tuple per cluster, ordered by the
    smallest raw index each cluster contains: *members* are the raw
    indices in ascending order and *canon* is the raw index of the
    cluster medoid (``canonical_index``) — the phrasing to label the
    cluster with.  The shared skeleton behind
    ``cluster_text_contributions`` and the MCDA criteria recorder,
    which aggregates per-cluster data beyond the text label.
    """
    groups = cluster_by_similarity(
        list(range(len(raw))),
        text_of=lambda i: raw[i][text_key],
        threshold=threshold)
    return [(group,
             group[canonical_index(group,
                                   text_of=lambda i: raw[i][text_key])])
            for group in groups]


def cluster_text_contributions(
        raw: list[dict], since: int = 0,
        text_key: str = "text",
        threshold: float = 0.7) -> tuple[list[dict], list[dict]]:
    """Cluster raw text contributions into an order-independent view.

    *raw* is the full list of contribution dicts, each carrying
    *text_key* plus ``entity_id`` / ``entity_name``.  Returns
    ``(view, touched)``:

    * *view* — one dict per cluster,
      ``{"id", text_key, "entity_id", "entity_name"}`` — labelled with
      the cluster medoid (``canonical_index``) and attributed to its
      founder (the earliest, min-index member).  Ids are the cluster
      rank in min-index order.
    * *touched* — the subset of *view* whose cluster contains a
      contribution at index >= *since* (i.e. the current turn's
      additions), for the turn's response display.
    """
    view: list[dict] = []
    touched: list[dict] = []
    for cid, (group, canon) in enumerate(
            cluster_groups(raw, text_key=text_key, threshold=threshold), 1):
        founder = raw[min(group)]
        item = {"id": cid, text_key: raw[canon][text_key],
                "entity_id": founder["entity_id"],
                "entity_name": founder["entity_name"]}
        view.append(item)
        if any(i >= since for i in group):
            touched.append(item)
    return view, touched


def validate_string_list_payload(payload: dict, key: str, min_length: int,
                                 empty_error: str, item_error: str,
                                 reasoning_error: str) -> str:
    """Shared validator for ``{<key>: [str, ...], "reasoning": str}`` payloads.

    The structural checks are shared — a non-empty list of substantive
    strings plus a non-blank ``reasoning`` — while the phase-specific
    wording stays at the call site (``validate_ideas_payload`` /
    ``validate_thoughts_payload``).  *item_error* may reference
    ``{item!r}`` and ``{min_length}`` via :meth:`str.format`, so any
    literal braces in it must be doubled (``{{``).  Returns ``''``
    when the payload is usable, else the matching error string.
    """
    items = payload.get(key)
    if not isinstance(items, list) or not items:
        return empty_error
    for item in items:
        if not isinstance(item, str) or len(item.strip()) < min_length:
            return item_error.format(item=item, min_length=min_length)
    if not str(payload.get("reasoning") or "").strip():
        return reasoning_error
    return ""


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

    Tries a fenced JSON block first, then the inline (unfenced)
    objects enclosing ``"key"``, innermost first: only a ``{`` still
    unclosed at the key's position can enclose it, so one pass builds
    that open-brace stack and each candidate is scanned to its
    balanced closing brace — a lazy regex would truncate at the first
    inner brace, and requiring the key in first position would miss
    pretty-printed or reordered objects.  Returns the value only when
    it is a dict or list.  Candidates that fail to parse are logged
    and the next is tried; callers own the user-facing warning when
    nothing is extracted.
    """
    data = extract_json_block(content)
    if isinstance(data, dict) and isinstance(data.get(key), (dict, list)):
        return data[key]
    marker = content.find(f'"{key}"')
    if marker == -1:
        return None
    open_stack: list[int] = []
    for pos in range(marker):
        if content[pos] == "{":
            open_stack.append(pos)
        elif content[pos] == "}" and open_stack:
            open_stack.pop()
    for start in reversed(open_stack):
        end = _balanced_object_end(content, start)
        if end is None:
            continue
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


#: JSON-Schema primitive types this checker validates natively.
_JSON_PRIMITIVES = ("string", "number", "integer", "boolean")


def check_payload_schema(payload: dict, schema: dict) -> str:
    """Structurally validate ``payload`` against a JSON-Schema ``parameters``.

    A library-free gate run *before* a handler's semantic ``validate_output``
    so handlers never ``KeyError`` on a missing field and the user gets a
    precise message.  Checks required keys, primitive types, ``enum``,
    numeric ``minimum``/``maximum``, and recurses one level into ``array``
    items and nested ``object`` ``properties``/``additionalProperties``.

    Returns ``""`` when acceptable, else a human-readable error.
    """
    if not isinstance(payload, dict):
        return "Input must be a set of fields."
    props = schema.get("properties", {})
    for key in schema.get("required", []):
        if key not in payload:
            return f"Missing required field: '{key}'."
    for key, value in payload.items():
        if key in props:
            err = _check_value(value, props[key], key)
            if err:
                return err
    return ""


def _check_value(value: Any, prop: dict, key: str) -> str:
    """Validate one value against its property subschema (see caller)."""
    enum = prop.get("enum")
    if enum is not None and value not in enum:
        return f"'{key}' must be one of {enum}."
    ptype = prop.get("type")
    if ptype in ("number", "integer"):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return f"'{key}' must be a number."
        if ptype == "integer" and not float(value).is_integer():
            return f"'{key}' must be a whole number."
        if "minimum" in prop and value < prop["minimum"]:
            return f"'{key}' must be >= {prop['minimum']}."
        if "maximum" in prop and value > prop["maximum"]:
            return f"'{key}' must be <= {prop['maximum']}."
    elif ptype == "string":
        if not isinstance(value, str):
            return f"'{key}' must be text."
    elif ptype == "boolean":
        if not isinstance(value, bool):
            return f"'{key}' must be true or false."
    elif ptype == "array":
        if not isinstance(value, list):
            return f"'{key}' must be a list."
        items = prop.get("items", {})
        for item in value:
            err = _check_value(item, items, key)
            if err:
                return err
    elif ptype == "object":
        return _check_object(value, prop, key)
    return ""


def _check_object(value: Any, prop: dict, key: str) -> str:
    """Validate a nested object's properties / additionalProperties values."""
    if not isinstance(value, dict):
        return f"'{key}' must be a set of fields."
    sub_props = prop.get("properties")
    if isinstance(sub_props, dict) and sub_props:
        for req in prop.get("required", []):
            if req not in value:
                return f"'{key}' is missing '{req}'."
        for k, v in value.items():
            if k in sub_props:
                err = _check_value(v, sub_props[k], f"{key}.{k}")
                if err:
                    return err
        return ""
    add = prop.get("additionalProperties")
    if isinstance(add, dict):
        for k, v in value.items():
            err = _check_value(v, add, f"{key}.{k}")
            if err:
                return err
    return ""
