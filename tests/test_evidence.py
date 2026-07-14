"""Tests for evidence-tracked phases (#28)."""
from consensus.methods.base import Phase
from consensus.evidence import (
    EVIDENCE_TOOL_NAMES,
    GroundingResult,
    classify_turn_grounding,
)
from consensus.tools import ToolCallRecord


class TestPhaseTrackEvidence:
    def test_defaults_to_false(self):
        p = Phase(name="x", display_name="X")
        assert p.track_evidence is False

    def test_can_opt_in(self):
        p = Phase(name="x", display_name="X", track_evidence=True)
        assert p.track_evidence is True


def _tc(tool_name, arguments=None, is_error=False):
    return ToolCallRecord(
        tool_name=tool_name, arguments=arguments or {}, result="ok",
        is_error=is_error,
    )


class TestClassifyToolPath:
    def test_doc_ask_call_is_grounded(self):
        res = classify_turn_grounding(
            "Per the document, X.",
            [_tc("doc_ask", {"document_id": 3, "question": "what is X?"})],
        )
        assert res.grounded is True
        assert res.sources == [
            {"type": "document", "document_id": 3,
             "detail": "what is X?", "tool": "doc_ask"}
        ]

    def test_web_search_call_is_grounded(self):
        res = classify_turn_grounding(
            "Searching.", [_tc("web_search", {"query": "climate data"})])
        assert res.grounded is True
        assert res.sources == [
            {"type": "web_search", "query": "climate data",
             "tool": "web_search"}
        ]

    def test_fetch_webpage_call_is_grounded(self):
        res = classify_turn_grounding(
            "Read it.", [_tc("fetch_webpage", {"url": "https://a.example"})])
        assert res.grounded is True
        assert res.sources == [
            {"type": "web", "url": "https://a.example",
             "tool": "fetch_webpage"}
        ]

    def test_errored_evidence_call_is_not_grounded(self):
        res = classify_turn_grounding(
            "Tried.", [_tc("doc_ask", {"document_id": 3}, is_error=True)])
        assert res.grounded is False
        assert res.sources == []

    def test_non_evidence_tool_is_not_grounded(self):
        res = classify_turn_grounding("Listing.", [_tc("doc_list", {})])
        assert res.grounded is False
        assert res.sources == []

    def test_no_tool_calls_and_no_citation_is_not_grounded(self):
        res = classify_turn_grounding("Just reasoning.", [])
        assert res.grounded is False
        assert res.sources == []

    def test_evidence_tool_set_membership(self):
        assert "doc_ask" in EVIDENCE_TOOL_NAMES
        assert "web_search" in EVIDENCE_TOOL_NAMES
        assert "doc_list" not in EVIDENCE_TOOL_NAMES
        assert "doc_add" not in EVIDENCE_TOOL_NAMES

    def test_doc_get_chapter_detail_uses_header(self):
        res = classify_turn_grounding(
            "Per the section.",
            [_tc("doc_get_chapter",
                 {"document_id": 5, "header": "Methods"})],
        )
        assert res.grounded is True
        assert res.sources == [
            {"type": "document", "document_id": 5,
             "detail": "Methods", "tool": "doc_get_chapter"}
        ]

    def test_doc_summary_detail_uses_char_range(self):
        res = classify_turn_grounding(
            "Summarised.",
            [_tc("doc_summary",
                 {"document_id": 7, "from_char": 0, "to_char": 500})],
        )
        assert res.grounded is True
        assert res.sources == [
            {"type": "document", "document_id": 7,
             "detail": "chars 0-500", "tool": "doc_summary"}
        ]

    def test_doc_get_text_detail_uses_char_range(self):
        res = classify_turn_grounding(
            "Read the passage.",
            [_tc("doc_get_text",
                 {"document_id": 9, "from_char": 100, "to_char": 250})],
        )
        assert res.grounded is True
        assert res.sources == [
            {"type": "document", "document_id": 9,
             "detail": "chars 100-250", "tool": "doc_get_text"}
        ]

    def test_doc_get_sections_has_empty_detail(self):
        res = classify_turn_grounding(
            "Listed sections.",
            [_tc("doc_get_sections", {"document_id": 11})],
        )
        assert res.grounded is True
        assert res.sources == [
            {"type": "document", "document_id": 11,
             "detail": "", "tool": "doc_get_sections"}
        ]


class TestClassifyInlinePath:
    def test_bare_url_is_grounded(self):
        res = classify_turn_grounding(
            "See https://example.org/paper for details.", [])
        assert res.grounded is True
        assert res.sources == [
            {"type": "web", "url": "https://example.org/paper"}
        ]

    def test_evidence_marker_doc_ref_is_grounded(self):
        res = classify_turn_grounding(
            "This holds [evidence: doc:5].", [])
        assert res.grounded is True
        assert res.sources == [{"type": "inline", "ref": "doc:5"}]

    def test_evidence_marker_url_ref_is_grounded(self):
        res = classify_turn_grounding(
            "As shown [evidence: https://a.example/x].", [])
        assert res.grounded is True
        assert res.sources == [
            {"type": "inline", "ref": "https://a.example/x"}
        ]

    def test_plain_text_is_not_grounded(self):
        res = classify_turn_grounding("No citation here at all.", [])
        assert res.grounded is False
        assert res.sources == []

    def test_tool_and_inline_sources_combine(self):
        res = classify_turn_grounding(
            "Per docs and https://a.example.",
            [_tc("doc_ask", {"document_id": 1, "question": "q"})])
        assert res.grounded is True
        assert len(res.sources) == 2
