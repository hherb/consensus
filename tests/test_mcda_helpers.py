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

    def test_merging_is_order_independent_and_medoid_labelled(self, alice,
                                                              bob):
        forward: dict = {}
        record_options(forward, alice, ["Buy a commercial solution now"])
        record_options(forward, bob, ["Buy a commercial solution"])
        reverse: dict = {}
        record_options(reverse, bob, ["Buy a commercial solution"])
        record_options(reverse, alice, ["Buy a commercial solution now"])
        assert len(forward["options"]) == len(reverse["options"]) == 1
        assert (forward["options"][0]["text"]
                == reverse["options"][0]["text"]
                == "Buy a commercial solution now")  # medoid = longer phrasing


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

    def test_touched_deduplicates_merged_criteria(self, alice):
        """Two similar names in one submission touch one criterion once."""
        state: dict = {}
        touched = record_criteria(state, alice, [
            {"name": "Total cost", "weight": 4},
            {"name": "Total Cost", "weight": 2},
        ])
        assert len(state["criteria"]) == 1
        assert len(touched) == 1
        assert state["criteria"][0]["weight_votes"] == {"1": 2}

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

    def test_weight_aggregation_is_order_independent(self, alice, bob):
        forward: dict = {}
        record_criteria(forward, alice,
                        [{"name": "Total cost of ownership", "weight": 4}])
        record_criteria(forward, bob,
                        [{"name": "Total cost of ownership now", "weight": 2}])
        reverse: dict = {}
        record_criteria(reverse, bob,
                        [{"name": "Total cost of ownership now", "weight": 2}])
        record_criteria(reverse, alice,
                        [{"name": "Total cost of ownership", "weight": 4}])
        assert len(forward["criteria"]) == len(reverse["criteria"]) == 1
        assert (forward["criteria"][0]["weight_votes"]
                == reverse["criteria"][0]["weight_votes"]
                == {"1": 4, "2": 2})
        # medoid label is order-independent (longer phrasing wins the tie)
        assert (forward["criteria"][0]["name"]
                == reverse["criteria"][0]["name"]
                == "Total cost of ownership now")


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

    def test_unknown_labels_dropped(self, alice):
        state = two_options_two_criteria(alice)
        kept = record_scores(state, alice, {
            "O1": {"C1": 4, "C9": 2},
            "Option 2": {"C1": 3},
        })
        assert kept == 1
        assert state["scores"]["1"] == {"O1": {"C1": 4}}

    def test_all_unknown_labels_records_nothing(self, alice):
        """Mislabelled free text must not count its author as a scorer.

        A stored-but-unaggregatable matrix would default every cell in
        ``participant_totals``, inflating divergence.
        """
        state = two_options_two_criteria(alice)
        assert record_scores(state, alice,
                             {"Option 1": {"Cost": 4}}) == 0
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


# ---------------------------------------------------------------------------
# Aggregation, sensitivity, artifact & formatting (Task 2)
# ---------------------------------------------------------------------------

from consensus.methods.phases._mcda_analysis import (  # noqa: E402
    build_decision_artifact,
    divergence_by_option,
    format_criteria,
    format_decision_artifact,
    format_divergence,
    format_mean_score_matrix,
    format_options,
    format_score_table,
    format_sensitivity,
    format_weighted_ranking,
    mean_scores,
    participant_totals,
    ranked_options,
    sensitivity_report,
    weighted_totals,
)


def scored_state(alice: Entity, bob: Entity) -> dict:
    """Two options, two criteria (weights 4 and 2), two scorers.

    Mean scores: O1 = {C1: 4.0, C2: 2.0}, O2 = {C1: 2.0, C2: 5.0}.
    Weighted totals: O1 = 4*4 + 2*2 = 20.0, O2 = 4*2 + 2*5 = 18.0.
    """
    state = two_options_two_criteria(alice)
    record_scores(state, alice, {"O1": {"C1": 4, "C2": 2},
                                 "O2": {"C1": 1, "C2": 5}})
    record_scores(state, bob, {"O1": {"C1": 4, "C2": 2},
                               "O2": {"C1": 3, "C2": 5}})
    return state


class TestAggregation:
    def test_mean_scores(self, alice, bob):
        means = mean_scores(scored_state(alice, bob))
        assert means["O1"] == {"C1": 4.0, "C2": 2.0}
        assert means["O2"] == {"C1": 2.0, "C2": 5.0}

    def test_unscored_cell_defaults_to_midpoint(self, alice):
        state = two_options_two_criteria(alice)
        record_scores(state, alice, {"O1": {"C1": 4}})
        means = mean_scores(state)
        assert means["O1"]["C2"] == float(DEFAULT_SCORE)
        assert means["O2"]["C1"] == float(DEFAULT_SCORE)

    def test_weighted_totals(self, alice, bob):
        totals = weighted_totals(scored_state(alice, bob))
        assert totals == {1: 20.0, 2: 18.0}

    def test_ranked_options_sorted_desc(self, alice, bob):
        ranking = ranked_options(scored_state(alice, bob))
        assert [r["id"] for r in ranking] == [1, 2]
        assert ranking[0]["total"] == 20.0
        assert ranking[0]["text"] == "Build the feature in-house"

    def test_ranked_options_tie_breaks_by_lower_id(self, alice):
        state = two_options_two_criteria(alice)
        record_scores(state, alice, {"O1": {"C1": 3, "C2": 3},
                                     "O2": {"C1": 3, "C2": 3}})
        ranking = ranked_options(state)
        assert [r["id"] for r in ranking] == [1, 2]

    def test_participant_totals_use_own_scores(self, alice, bob):
        per = participant_totals(scored_state(alice, bob))
        # Alice: O2 = 4*1 + 2*5 = 14; Bob: O2 = 4*3 + 2*5 = 22.
        assert per["1"][2] == 14.0
        assert per["2"][2] == 22.0

    def test_divergence_is_spread_of_participant_totals(self, alice, bob):
        div = divergence_by_option(scored_state(alice, bob))
        assert div[1] == 0.0
        assert div[2] == 8.0

    def test_divergence_zero_with_single_scorer(self, alice):
        state = two_options_two_criteria(alice)
        record_scores(state, alice, {"O1": {"C1": 4}})
        assert divergence_by_option(state) == {1: 0.0, 2: 0.0}


class TestSensitivity:
    def test_robust_ranking_reports_no_pivots(self, alice, bob):
        state = two_options_two_criteria(alice)
        # O1 dominates on every criterion: no variation can flip it.
        record_scores(state, alice, {"O1": {"C1": 5, "C2": 5},
                                     "O2": {"C1": 1, "C2": 1}})
        report = sensitivity_report(state)
        assert report["baseline_winner_id"] == 1
        assert report["pivotal_criteria"] == []
        assert report["close_call"] is False

    def test_pivotal_criterion_detected(self, alice, bob):
        report = sensitivity_report(scored_state(alice, bob))
        # O1 wins only through C1: excluding C1 gives O1=2*2=4 < O2=2*5=10;
        # doubling C2 gives O1=16+8=24 < O2=8+20=28.  Both variations flip.
        variations = {(p["criterion_id"], p["variation"])
                      for p in report["pivotal_criteria"]}
        assert (1, "excluded") in variations
        assert (2, "doubled") in variations
        assert all(p["new_winner_id"] == 2
                   for p in report["pivotal_criteria"])

    def test_close_call_flagged(self, alice, bob):
        report = sensitivity_report(scored_state(alice, bob))
        # Margin 2.0 on a top total of 20.0 = 10% > 5%: not close.
        assert report["close_call"] is False
        assert report["margin"] == 2.0

    def test_single_option_short_circuits(self, alice):
        state: dict = {}
        record_options(state, alice, ["Build the feature in-house"])
        record_criteria(state, alice, [{"name": "Total cost", "weight": 4}])
        report = sensitivity_report(state)
        assert report["baseline_winner_id"] == 1
        assert report["pivotal_criteria"] == []


class TestDecisionArtifact:
    def test_builds_and_stores_artifact(self, alice, bob):
        state = scored_state(alice, bob)
        artifact = build_decision_artifact(
            state, 1, "Wins on the dominant cost criterion.",
            ["C1 is pivotal."])
        assert state["decision_artifact"] is artifact
        assert artifact["method"] == "decision_matrix"
        assert artifact["recommended_option_id"] == 1
        assert artifact["recommended_option"] == (
            "Build the feature in-house")
        assert artifact["rationale"].startswith("Wins on")
        assert artifact["caveats"] == ["C1 is pivotal."]
        assert [r["option_id"] for r in artifact["ranking"]] == [1, 2]
        assert artifact["ranking"][0]["weighted_total"] == 20.0
        assert artifact["ranking"][0]["mean_scores"] == {"C1": 4.0,
                                                         "C2": 2.0}
        assert artifact["criteria"][0] == {"id": 1, "name": "Total cost",
                                           "weight": 4.0}
        assert {d["option_id"]: d["spread"]
                for d in artifact["divergence"]} == {1: 0.0, 2: 8.0}
        assert artifact["scorers"] == 2
        assert artifact["sensitivity"]["baseline_winner_id"] == 1

    def test_artifact_is_json_serialisable(self, alice, bob):
        artifact = build_decision_artifact(
            scored_state(alice, bob), 2, "Rationale.", [])
        assert json.loads(json.dumps(artifact)) == artifact

    def test_zero_scorer_artifact_carries_caveat(self, alice):
        """With no scores the ranking is contentless — say so."""
        state = two_options_two_criteria(alice)
        artifact = build_decision_artifact(state, 1, "Rationale.", [])
        assert artifact["scorers"] == 0
        assert any("scale midpoint" in c for c in artifact["caveats"])

    def test_scored_artifact_has_no_midpoint_caveat(self, alice, bob):
        artifact = build_decision_artifact(
            scored_state(alice, bob), 1, "Rationale.", [])
        assert not any("scale midpoint" in c for c in artifact["caveats"])


class TestFormatting:
    def test_format_options(self, alice, bob):
        text = format_options(scored_state(alice, bob))
        assert "O1: Build the feature in-house" in text
        assert "O2: Buy a commercial solution" in text
        assert format_options({}) == "  (No options)"

    def test_format_criteria_shows_weights_and_votes(self, alice, bob):
        text = format_criteria(scored_state(alice, bob))
        assert "C1: Total cost — weight 4.0 (1 vote(s))" in text
        assert format_criteria({}) == "  (No criteria)"

    def test_format_score_table_renders_matrix(self, alice, bob):
        state = scored_state(alice, bob)
        table = format_score_table(state["scores"]["1"], state)
        assert "| **O1**" in table
        assert "C1 (Total cost)" in table

    def test_format_score_table_marks_missing_cells(self, alice):
        state = two_options_two_criteria(alice)
        record_scores(state, alice, {"O1": {"C1": 4}})
        assert "?" in format_score_table(state["scores"]["1"], state)

    def test_format_mean_score_matrix(self, alice, bob):
        assert "| **O1**" in format_mean_score_matrix(
            scored_state(alice, bob))

    def test_format_score_table_rounds_long_floats(self, alice):
        state = two_options_two_criteria(alice)
        table = format_score_table(
            {"O1": {"C1": 10 / 3}}, state)
        assert "3.33" in table
        assert "3.33333" not in table

    def test_format_weighted_ranking(self, alice, bob):
        text = format_weighted_ranking(scored_state(alice, bob))
        assert "1. O1: Build the feature in-house — weighted total 20.0" \
            in text

    def test_format_divergence(self, alice, bob):
        assert "O2: Buy a commercial solution — spread 8.0" in \
            format_divergence(scored_state(alice, bob))

    def test_format_sensitivity_names_pivots(self, alice, bob):
        text = format_sensitivity(scored_state(alice, bob))
        assert "Pivotal" in text
        assert "C1 (Total cost) excluded" in text

    def test_format_sensitivity_robust_message(self, alice):
        state = two_options_two_criteria(alice)
        record_scores(state, alice, {"O1": {"C1": 5, "C2": 5},
                                     "O2": {"C1": 1, "C2": 1}})
        assert "robust" in format_sensitivity(state)

    def test_format_decision_artifact(self, alice, bob):
        artifact = build_decision_artifact(
            scored_state(alice, bob), 1, "Cost dominates.",
            ["Revisit if costs change."])
        text = format_decision_artifact(artifact)
        assert "**Decision: Build the feature in-house**" in text
        assert "Cost dominates." in text
        assert "weighted total 20.0" in text
        assert "Revisit if costs change." in text
