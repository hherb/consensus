"""Tests for Counterfactual Stress Testing phase handlers and helpers."""

import pytest

from consensus.methods.phases._counterfactual_helpers import (
    extract_impact_score,
    classify_claim,
    format_results_table,
)


class TestExtractImpactScore:
    def test_standard_tag(self):
        content = "The conclusion falls apart entirely. [IMPACT: 5]"
        assert extract_impact_score(content) == 5

    def test_low_impact(self):
        content = "Not much changes. [IMPACT: 1]"
        assert extract_impact_score(content) == 1

    def test_mid_impact(self):
        content = "Some elements weaken. [IMPACT: 3]"
        assert extract_impact_score(content) == 3

    def test_no_tag(self):
        content = "I think the impact is moderate."
        assert extract_impact_score(content) is None

    def test_tag_in_middle(self):
        content = "Analysis here. [IMPACT: 4] More text after."
        assert extract_impact_score(content) == 4

    def test_out_of_range_high(self):
        content = "[IMPACT: 7]"
        assert extract_impact_score(content) is None

    def test_out_of_range_zero(self):
        content = "[IMPACT: 0]"
        assert extract_impact_score(content) is None

    def test_whitespace_variations(self):
        content = "[IMPACT:  3 ]"
        assert extract_impact_score(content) == 3

    def test_lowercase_ignored(self):
        content = "[impact: 3]"
        assert extract_impact_score(content) is None


class TestClassifyClaim:
    def test_load_bearing(self):
        assert classify_claim(4.5) == "LOAD-BEARING"

    def test_load_bearing_threshold(self):
        assert classify_claim(4.0) == "LOAD-BEARING"

    def test_supportive(self):
        assert classify_claim(3.0) == "SUPPORTIVE"

    def test_supportive_threshold(self):
        assert classify_claim(2.0) == "SUPPORTIVE"

    def test_decorative(self):
        assert classify_claim(1.5) == "DECORATIVE"

    def test_decorative_low(self):
        assert classify_claim(1.0) == "DECORATIVE"


class TestFormatResultsTable:
    def test_basic_table(self):
        results = [
            {
                "claim_id": 1,
                "claim_text": "Claim one text",
                "scores": {"Alice": 5, "Bob": 4},
                "avg_score": 4.5,
                "classification": "LOAD-BEARING",
            },
            {
                "claim_id": 2,
                "claim_text": "Claim two text",
                "scores": {"Alice": 1, "Bob": 2},
                "avg_score": 1.5,
                "classification": "DECORATIVE",
            },
        ]
        table = format_results_table(results)
        assert "Claim one text" in table
        assert "4.5" in table
        assert "LOAD-BEARING" in table
        assert "DECORATIVE" in table

    def test_empty_results(self):
        table = format_results_table([])
        assert "No claims" in table or table == ""

    def test_none_scores(self):
        results = [
            {
                "claim_id": 1,
                "claim_text": "Untested claim",
                "scores": {},
                "avg_score": None,
                "classification": None,
            },
        ]
        table = format_results_table(results)
        assert "Untested claim" in table
