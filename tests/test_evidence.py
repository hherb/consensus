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
