"""Evidence-tracked phases (#28).

Turn-level provenance tracking: classify each contribution as grounded
(backed by a tool-sourced or inline citation) or reasoning-based, record
what it rests on into ``method_state["evidence_log"]``, annotate the
display text, and summarise the evidentiary basis for the conclusion.

Soft by design: an ungrounded turn is annotated and logged, never
rejected.  Classification is computed in code, never by the model.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .tools import ToolCallRecord

#: Participant tools whose successful use grounds a contribution in
#: retrievable evidence.  Excludes management/navigation tools
#: (``doc_add``, ``doc_list``, ``doc_get_length``).
EVIDENCE_TOOL_NAMES: frozenset[str] = frozenset({
    "doc_ask",
    "doc_get_text",
    "doc_summary",
    "doc_get_sections",
    "doc_get_chapter",
    "web_search",
    "fetch_webpage",
})

#: Explicit inline citation marker, e.g. ``[evidence: doc:5]`` or
#: ``[evidence: https://…]``.  Inserted by the frontend "Attach
#: evidence" control; also typeable by hand.
EVIDENCE_MARKER_RE = re.compile(r"\[evidence:\s*([^\]]+?)\s*\]",
                                re.IGNORECASE)

#: Bare http(s) URL.
URL_RE = re.compile(r"https?://[^\s<>\]]+")


@dataclass
class GroundingResult:
    """Whether a turn is grounded and the sources it rests on."""

    grounded: bool
    sources: list[dict] = field(default_factory=list)


def _strip_trailing_punct(url: str) -> str:
    """Trim sentence punctuation a URL commonly picks up in prose."""
    while url and url[-1] in ".,;:!?\"'":
        url = url[:-1]
    # A closing paren belongs to the URL only if it has a matching open one.
    if url.endswith(")") and url.count("(") < url.count(")"):
        url = url[:-1]
    return url


def _inline_sources(content: str) -> list[dict]:
    """Parse inline citations (explicit markers, then bare URLs)."""
    sources: list[dict] = []
    marked_spans: list[tuple[int, int]] = []
    for m in EVIDENCE_MARKER_RE.finditer(content or ""):
        sources.append({"type": "inline", "ref": m.group(1).strip()})
        marked_spans.append(m.span())
    for m in URL_RE.finditer(content or ""):
        # Skip URLs already captured inside an [evidence: …] marker.
        if any(s <= m.start() < e for s, e in marked_spans):
            continue
        sources.append(
            {"type": "web", "url": _strip_trailing_punct(m.group(0))})
    return sources


def _document_detail(name: str, args: dict) -> str:
    """Extract a human-readable identifying detail for a document tool.

    Each document tool exposes a different identifying argument (see the
    schemas in :mod:`consensus.tools_document`): ``doc_ask`` a
    ``question``, ``doc_get_chapter`` a ``header``, and
    ``doc_get_text``/``doc_summary`` a ``from_char``/``to_char`` range.
    ``doc_get_sections`` has no identifying argument, so returns ``""``.
    """
    if name == "doc_ask":
        return args.get("question", "")
    if name == "doc_get_chapter":
        return args.get("header", "")
    if name in {"doc_get_text", "doc_summary"}:
        from_char = args.get("from_char")
        to_char = args.get("to_char")
        if from_char is not None and to_char is not None:
            return f"chars {from_char}-{to_char}"
        return ""
    return ""


def _source_from_tool_call(tc: "ToolCallRecord") -> dict | None:
    """Derive a source descriptor from a tool call's name + arguments.

    Robust extraction from ``arguments`` only — ``ToolCallRecord`` drops
    the richer ``ToolResult.metadata``.  Returns ``None`` for tools that
    do not ground a contribution.
    """
    name = tc.tool_name
    args = tc.arguments or {}
    if name in {"doc_ask", "doc_get_text", "doc_summary",
                "doc_get_sections", "doc_get_chapter"}:
        detail = _document_detail(name, args)
        return {"type": "document",
                "document_id": args.get("document_id"),
                "detail": detail, "tool": name}
    if name == "web_search":
        return {"type": "web_search",
                "query": args.get("query", ""), "tool": name}
    if name == "fetch_webpage":
        return {"type": "web", "url": args.get("url", ""), "tool": name}
    return None


def classify_turn_grounding(content: str,
                            tool_calls: list["ToolCallRecord"],
                            ) -> GroundingResult:
    """Classify a turn as grounded or reasoning-based.

    Two detection paths, evaluated together — a turn is grounded if
    either finds a citation:

    * Tool path: a successful (``is_error is False``) call to a tool in
      :data:`EVIDENCE_TOOL_NAMES`.
    * Inline path (added later): a parseable citation in ``content``.
    """
    sources: list[dict] = []
    for tc in tool_calls or []:
        if getattr(tc, "is_error", False):
            continue
        if tc.tool_name in EVIDENCE_TOOL_NAMES:
            src = _source_from_tool_call(tc)
            if src is not None:
                sources.append(src)
    sources.extend(_inline_sources(content))
    return GroundingResult(grounded=bool(sources), sources=sources)
