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
        web = [s for s in res.sources if s["type"] == "web"]
        assert web == [{"type": "web", "url": "https://a.example"}]

    def test_bare_url_trailing_period_is_stripped(self):
        res = classify_turn_grounding("See https://a.example.", [])
        assert res.sources == [
            {"type": "web", "url": "https://a.example"}
        ]

    def test_parenthesized_url_is_stripped(self):
        res = classify_turn_grounding(
            "(https://example.org/paper).", [])
        assert res.sources == [
            {"type": "web", "url": "https://example.org/paper"}
        ]

    def test_bare_url_trailing_comma_is_stripped(self):
        res = classify_turn_grounding(
            "See https://example.org/paper, and more.", [])
        assert res.sources == [
            {"type": "web", "url": "https://example.org/paper"}
        ]

    def test_url_with_matched_parens_is_preserved(self):
        res = classify_turn_grounding(
            "See https://en.wikipedia.org/wiki/Foo_(bar) now.", [])
        assert res.sources == [
            {"type": "web",
             "url": "https://en.wikipedia.org/wiki/Foo_(bar)"}
        ]


from consensus.evidence import record_and_annotate_evidence
from consensus.models import Discussion, Entity, EntityType


def _discussion():
    d = Discussion(topic="t")
    d.method_state = {"current_phase": "test_crux"}
    return d


def _entity(eid=1, name="Alice"):
    return Entity(id=eid, name=name, entity_type=EntityType.AI)


class TestRecordAndAnnotate:
    def test_grounded_turn_logs_and_annotates(self):
        d = _discussion()
        out = record_and_annotate_evidence(
            d, _entity(), turn_number=4, content="Per docs.",
            tool_calls=[_tc("doc_ask", {"document_id": 3, "question": "q"})])
        log = d.method_state["evidence_log"]
        assert len(log) == 1
        entry = log[0]
        assert entry["entity_id"] == 1
        assert entry["entity_name"] == "Alice"
        assert entry["turn"] == 4
        assert entry["phase"] == "test_crux"
        assert entry["grounded"] is True
        assert entry["sources"][0]["document_id"] == 3
        assert "sources:" in out.lower()
        assert out.startswith("Per docs.")

    def test_ungrounded_turn_logs_and_annotates(self):
        d = _discussion()
        out = record_and_annotate_evidence(
            d, _entity(2, "Bob"), turn_number=5,
            content="Pure reasoning.", tool_calls=[])
        entry = d.method_state["evidence_log"][0]
        assert entry["grounded"] is False
        assert entry["sources"] == []
        assert "reasoning-based" in out.lower()

    def test_appends_across_turns(self):
        d = _discussion()
        record_and_annotate_evidence(
            d, _entity(), 1, "a", [_tc("web_search", {"query": "x"})])
        record_and_annotate_evidence(d, _entity(2, "Bob"), 2, "b", [])
        assert len(d.method_state["evidence_log"]) == 2
