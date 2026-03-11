"""Tests for Recursive Decomposition helper utilities."""

import pytest
from consensus.methods.phases._decomposition_helpers import (
    extract_subquestion_analyses,
)


class TestExtractSubquestionAnalyses:
    def test_extracts_bold_subquestion_headers(self):
        content = (
            "**Sub-question 1:** The economy is driven by consumer spending.\n\n"
            "**Sub-question 2:** Interest rates affect investment decisions.\n\n"
            "**Sub-question 3:** Trade policy shapes export markets."
        )
        result = extract_subquestion_analyses(content, 3)
        assert len(result) == 3
        assert 0 in result
        assert "consumer spending" in result[0]
        assert "interest rates" in result[1].lower()
        assert "trade policy" in result[2].lower()

    def test_extracts_short_q_headers(self):
        content = (
            "**Q1:** Analysis of first question.\n\n"
            "**Q2:** Analysis of second question."
        )
        result = extract_subquestion_analyses(content, 2)
        assert len(result) == 2
        assert "first question" in result[0].lower()

    def test_extracts_bold_numbered_headers(self):
        content = (
            "**1.** First analysis paragraph.\n\n"
            "**2.** Second analysis paragraph."
        )
        result = extract_subquestion_analyses(content, 2)
        assert len(result) == 2
        assert "First analysis" in result[0]

    def test_fallback_when_no_headers_detected(self):
        content = "This is a free-form response without any structure."
        result = extract_subquestion_analyses(content, 3)
        assert len(result) == 3
        assert result[0] == content
        assert result[1] == content
        assert result[2] == content

    def test_handles_extra_sections_beyond_num_subquestions(self):
        content = (
            "**Sub-question 1:** First.\n\n"
            "**Sub-question 2:** Second.\n\n"
            "**Sub-question 3:** Third.\n\n"
            "**Sub-question 4:** Extra one."
        )
        result = extract_subquestion_analyses(content, 3)
        assert len(result) == 3

    def test_handles_fewer_sections_than_expected(self):
        content = (
            "**Sub-question 1:** Only this one.\n\n"
        )
        result = extract_subquestion_analyses(content, 3)
        assert len(result) == 3
        assert "Only this one" in result[0]
        assert result[1] == ""
        assert result[2] == ""

    def test_multiline_analysis_per_subquestion(self):
        content = (
            "**Sub-question 1:** First line of analysis.\n"
            "Continued analysis with more detail.\n"
            "Even more detail here.\n\n"
            "**Sub-question 2:** Second question analysis."
        )
        result = extract_subquestion_analyses(content, 2)
        assert "Continued analysis" in result[0]
        assert "Even more detail" in result[0]

    def test_zero_subquestions_returns_empty(self):
        result = extract_subquestion_analyses("Some content", 0)
        assert result == {}

    def test_empty_content_returns_fallback(self):
        result = extract_subquestion_analyses("", 2)
        assert len(result) == 2
        assert result[0] == ""
        assert result[1] == ""
