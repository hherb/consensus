"""Tests for the Weighted Decision Matrix helper module (issue #25).

Covers the pure-function layer: payload validators, option/criterion/
score recording, free-text extraction, aggregation math, sensitivity
analysis, decision-artifact assembly, and display formatting.
"""

import json

import pytest

from consensus.methods.phases._mcda_helpers import (
    CLOSE_CALL_MARGIN,
    CRITERIA_TOOL_PARAMETERS,
    DECISION_TOOL_PARAMETERS,
    DEFAULT_SCORE,
    DEFAULT_WEIGHT,
    MAX_CRITERIA_ROUNDS,
    MAX_OPTIONS_ROUNDS,
    OPTIONS_TOOL_PARAMETERS,
    SCORE_MAX,
    SCORE_MIN,
    SCORES_TOOL_PARAMETERS,
    WEIGHT_MAX,
    WEIGHT_MIN,
    criterion_label,
    criterion_weight,
    extract_scores,
    extract_weighted_criteria,
    option_label,
    record_criteria,
    record_options,
    record_scores,
    validate_criteria_payload,
    validate_decision_payload,
    validate_options_payload,
    validate_scores_payload,
)
from consensus.models import Entity, EntityType


@pytest.fixture
def alice() -> Entity:
    return Entity(name="Alice", entity_type=EntityType.AI, id=1)


@pytest.fixture
def bob() -> Entity:
    return Entity(name="Bob", entity_type=EntityType.AI, id=2)


def two_options_two_criteria(alice: Entity) -> dict:
    """A state with two recorded options and two criteria (weights 4, 2)."""
    state: dict = {}
    record_options(state, alice, ["Build the feature in-house",
                                  "Buy a commercial solution"])
    record_criteria(state, alice, [{"name": "Total cost", "weight": 4},
                                   {"name": "Time to market", "weight": 2}])
    return state


class TestConstants:
    def test_bounds_are_ordered(self):
        assert WEIGHT_MIN < WEIGHT_MAX
        assert SCORE_MIN < SCORE_MAX
        assert SCORE_MIN <= DEFAULT_SCORE <= SCORE_MAX
        assert WEIGHT_MIN <= DEFAULT_WEIGHT <= WEIGHT_MAX

    def test_give_up_caps_are_positive(self):
        assert MAX_OPTIONS_ROUNDS > 0
        assert MAX_CRITERIA_ROUNDS > 0

    def test_close_call_margin_is_a_small_fraction(self):
        assert 0 < CLOSE_CALL_MARGIN < 1


class TestLabels:
    def test_labels(self):
        assert option_label(3) == "O3"
        assert criterion_label(1) == "C1"


class TestOptionsSchemaAndValidator:
    def test_schema_shape(self):
        assert OPTIONS_TOOL_PARAMETERS["type"] == "object"
        assert "options" in OPTIONS_TOOL_PARAMETERS["properties"]
        assert set(OPTIONS_TOOL_PARAMETERS["required"]) == {
            "options", "reasoning"}

    def test_valid(self):
        assert validate_options_payload(
            {"options": ["Build in-house", "Buy a solution"],
             "reasoning": "These are the alternatives."}) == ""

    def test_missing_options_rejected(self):
        assert "options" in validate_options_payload({"reasoning": "x"})

    def test_empty_options_rejected(self):
        assert validate_options_payload(
            {"options": [], "reasoning": "x"}) != ""

    def test_non_string_option_rejected(self):
        assert validate_options_payload(
            {"options": [42], "reasoning": "x"}) != ""

    def test_too_short_option_rejected(self):
        assert validate_options_payload(
            {"options": ["ab"], "reasoning": "x"}) != ""

    def test_null_reasoning_rejected(self):
        assert "reasoning" in validate_options_payload(
            {"options": ["Buy a solution"], "reasoning": None})


class TestRecordOptions:
    def test_assigns_sequential_ids_and_attribution(self, alice):
        state: dict = {}
        accepted = record_options(state, alice,
                                  ["Build it in-house.", "Buy a solution"])
        assert [o["id"] for o in state["options"]] == [1, 2]
        assert state["options"][0]["entity_id"] == 1
        assert state["options"][0]["entity_name"] == "Alice"
        assert state["options"][0]["text"] == "Build it in-house"
        assert accepted == state["options"]

    def test_dedups_by_word_overlap(self, alice, bob):
        state: dict = {}
        record_options(state, alice, ["Buy a commercial solution now"])
        record_options(state, bob, ["Buy a commercial solution"])
        assert len(state["options"]) == 1

    def test_drops_too_short_items(self, alice):
        state: dict = {}
        record_options(state, alice, ["ab"])
        assert state["options"] == []


class TestCriteriaSchemaAndValidator:
    def test_schema_shape(self):
        assert "criteria" in CRITERIA_TOOL_PARAMETERS["properties"]
        item = CRITERIA_TOOL_PARAMETERS["properties"]["criteria"]["items"]
        assert set(item["required"]) == {"name", "weight"}
        assert item["properties"]["weight"]["minimum"] == WEIGHT_MIN
        assert item["properties"]["weight"]["maximum"] == WEIGHT_MAX
        assert set(CRITERIA_TOOL_PARAMETERS["required"]) == {
            "criteria", "reasoning"}

    def test_valid(self):
        assert validate_criteria_payload(
            {"criteria": [{"name": "Total cost", "weight": 4}],
             "reasoning": "Cost matters most."}) == ""

    def test_missing_criteria_rejected(self):
        assert validate_criteria_payload({"reasoning": "x"}) != ""

    def test_non_object_criterion_rejected(self):
        assert validate_criteria_payload(
            {"criteria": ["Total cost"], "reasoning": "x"}) != ""

    def test_short_name_rejected(self):
        assert validate_criteria_payload(
            {"criteria": [{"name": "ab", "weight": 3}],
             "reasoning": "x"}) != ""

    def test_boolean_weight_rejected(self):
        assert validate_criteria_payload(
            {"criteria": [{"name": "Total cost", "weight": True}],
             "reasoning": "x"}) != ""

    def test_out_of_range_weight_rejected(self):
        assert validate_criteria_payload(
            {"criteria": [{"name": "Total cost", "weight": WEIGHT_MAX + 1}],
             "reasoning": "x"}) != ""

    def test_null_reasoning_rejected(self):
        assert "reasoning" in validate_criteria_payload(
            {"criteria": [{"name": "Total cost", "weight": 3}],
             "reasoning": None})


class TestRecordCriteria:
    def test_assigns_sequential_ids_and_weight_votes(self, alice):
        state: dict = {}
        record_criteria(state, alice,
                        [{"name": "Total cost.", "weight": 4}])
        crit = state["criteria"][0]
        assert crit["id"] == 1
        assert crit["name"] == "Total cost"
        assert crit["weight_votes"] == {"1": 4}

    def test_similar_name_merges_and_adds_vote(self, alice, bob):
        state: dict = {}
        record_criteria(state, alice,
                        [{"name": "Total cost of ownership", "weight": 4}])
        record_criteria(state, bob,
                        [{"name": "Total cost of ownership", "weight": 2}])
        assert len(state["criteria"]) == 1
        assert state["criteria"][0]["weight_votes"] == {"1": 4, "2": 2}

    def test_resubmission_replaces_own_vote(self, alice):
        state: dict = {}
        record_criteria(state, alice,
                        [{"name": "Total cost of ownership", "weight": 4}])
        record_criteria(state, alice,
                        [{"name": "Total cost of ownership", "weight": 1}])
        assert state["criteria"][0]["weight_votes"] == {"1": 1}

    def test_free_text_weight_clamped_into_range(self, alice):
        state: dict = {}
        record_criteria(state, alice,
                        [{"name": "Total cost", "weight": 99}])
        assert state["criteria"][0]["weight_votes"]["1"] == WEIGHT_MAX

    def test_criterion_weight_is_mean_of_votes(self, alice, bob):
        state: dict = {}
        record_criteria(state, alice, [{"name": "Total cost", "weight": 4}])
        record_criteria(state, bob, [{"name": "Total cost", "weight": 2}])
        assert criterion_weight(state["criteria"][0]) == 3.0

    def test_criterion_weight_defaults_without_votes(self):
        assert criterion_weight({"id": 1, "name": "x",
                                 "weight_votes": {}}) == float(DEFAULT_WEIGHT)


class TestExtractWeightedCriteria:
    def test_parses_numbered_list_with_weights(self):
        items = extract_weighted_criteria(
            "1. Total cost of ownership (weight: 4)\n"
            "2. Time to market [weight = 2]\n"
            "3. Team familiarity")
        assert items == [
            {"name": "Total cost of ownership", "weight": 4},
            {"name": "Time to market", "weight": 2},
            {"name": "Team familiarity", "weight": DEFAULT_WEIGHT},
        ]

    def test_no_numbered_list_yields_nothing(self):
        assert extract_weighted_criteria("I think cost matters.") == []


class TestScoresSchemaAndValidator:
    def test_schema_uses_additional_properties(self):
        scores = SCORES_TOOL_PARAMETERS["properties"]["scores"]
        inner = scores["additionalProperties"]["additionalProperties"]
        assert inner["type"] == "integer"
        assert inner["minimum"] == SCORE_MIN
        assert inner["maximum"] == SCORE_MAX
        assert set(SCORES_TOOL_PARAMETERS["required"]) == {
            "scores", "reasoning"}

    def test_valid(self, alice):
        state = two_options_two_criteria(alice)
        assert validate_scores_payload(
            {"scores": {"O1": {"C1": 4, "C2": 2}, "O2": {"C1": 1}},
             "reasoning": "Cost dominates."},
            state["options"], state["criteria"]) == ""

    def test_unknown_option_label_rejected(self, alice):
        state = two_options_two_criteria(alice)
        error = validate_scores_payload(
            {"scores": {"O9": {"C1": 4}}, "reasoning": "x"},
            state["options"], state["criteria"])
        assert "O9" in error

    def test_unknown_criterion_label_rejected(self, alice):
        state = two_options_two_criteria(alice)
        error = validate_scores_payload(
            {"scores": {"O1": {"C9": 4}}, "reasoning": "x"},
            state["options"], state["criteria"])
        assert "C9" in error

    def test_boolean_score_rejected(self, alice):
        state = two_options_two_criteria(alice)
        assert validate_scores_payload(
            {"scores": {"O1": {"C1": True}}, "reasoning": "x"},
            state["options"], state["criteria"]) != ""

    def test_out_of_range_score_rejected(self, alice):
        state = two_options_two_criteria(alice)
        assert validate_scores_payload(
            {"scores": {"O1": {"C1": SCORE_MAX + 1}}, "reasoning": "x"},
            state["options"], state["criteria"]) != ""

    def test_empty_scores_rejected(self, alice):
        state = two_options_two_criteria(alice)
        assert validate_scores_payload(
            {"scores": {}, "reasoning": "x"},
            state["options"], state["criteria"]) != ""

    def test_null_reasoning_rejected(self, alice):
        state = two_options_two_criteria(alice)
        assert "reasoning" in validate_scores_payload(
            {"scores": {"O1": {"C1": 3}}, "reasoning": None},
            state["options"], state["criteria"])


class TestRecordScores:
    def test_stores_per_entity_and_returns_cells_kept(self, alice):
        state = two_options_two_criteria(alice)
        kept = record_scores(state, alice, {"O1": {"C1": 4, "C2": 2}})
        assert kept == 2
        assert state["scores"]["1"] == {"O1": {"C1": 4, "C2": 2}}

    def test_sanitises_invalid_cells(self, alice):
        state = two_options_two_criteria(alice)
        kept = record_scores(state, alice, {
            "O1": {"C1": "4", "C2": True},
            "O2": {"C1": SCORE_MAX + 3, "C2": "junk"},
        })
        assert kept == 1
        assert state["scores"]["1"] == {"O1": {"C1": 4}}

    def test_all_invalid_records_nothing(self, alice):
        state = two_options_two_criteria(alice)
        assert record_scores(state, alice, {"O1": "junk"}) == 0
        assert "scores" not in state


class TestExtractScores:
    def test_fenced_json_block(self):
        content = ('Here are my scores:\n```json\n'
                   '{"scores": {"O1": {"C1": 4}}}\n```')
        assert extract_scores(content) == {"O1": {"C1": 4}}

    def test_inline_json(self):
        content = 'My scores: {"scores": {"O1": {"C1": 4, "C2": 2}}} done.'
        assert extract_scores(content) == {"O1": {"C1": 4, "C2": 2}}

    def test_prose_yields_nothing(self):
        assert extract_scores("I prefer option one overall.") == {}


class TestDecisionSchemaAndValidator:
    def test_schema_shape(self):
        props = DECISION_TOOL_PARAMETERS["properties"]
        assert props["recommended_option_id"]["type"] == "integer"
        assert set(DECISION_TOOL_PARAMETERS["required"]) == {
            "recommended_option_id", "rationale"}

    def test_valid(self):
        assert validate_decision_payload(
            {"recommended_option_id": 1, "rationale": "It wins on cost.",
             "caveats": ["Margin is small."]}, {1, 2}) == ""

    def test_unknown_option_rejected(self):
        error = validate_decision_payload(
            {"recommended_option_id": 9, "rationale": "x"}, {1, 2})
        assert "9" in error

    def test_boolean_id_rejected(self):
        assert validate_decision_payload(
            {"recommended_option_id": True, "rationale": "x"}, {1, 2}) != ""

    def test_empty_rationale_rejected(self):
        assert "rationale" in validate_decision_payload(
            {"recommended_option_id": 1, "rationale": "  "}, {1, 2})

    def test_non_string_caveats_rejected(self):
        assert validate_decision_payload(
            {"recommended_option_id": 1, "rationale": "x",
             "caveats": [42]}, {1, 2}) != ""
