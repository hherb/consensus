"""Tests for Tree of Thoughts shared helpers (issue #26).

Pure-function coverage: payload validators, thought recording with
word-overlap dedup, score recording with per-thought merge, composite
and beam computation (risk inversion, midpoint defaults, deterministic
tie-breaks), eligibility narrowing after a prune, expansion recording,
the outcome artifact, JSON extraction, and display formatting.
"""

from consensus.methods.phases._tot_analysis import (
    build_tot_artifact,
    composite_of,
    compute_beam,
    format_beam,
    format_beam_trajectory,
    format_expansions,
    format_ranking,
    format_thoughts,
    thought_composites,
)
from consensus.methods.phases._tot_helpers import (
    BEAM_WIDTH,
    DEFAULT_DIMENSION_SCORE,
    DIMENSIONS,
    EXPANSIONS_TOOL_PARAMETERS,
    MAX_PROPOSE_ROUNDS,
    MAX_TOT_DEPTH,
    MIN_BEAM_SIZE,
    MIN_REFINEMENT_LENGTH,
    MIN_THOUGHT_LENGTH,
    SCORE_MAX,
    SCORE_MIN,
    SCORES_TOOL_PARAMETERS,
    STOP_CONVERGED,
    STOP_DEGENERATE,
    STOP_DEPTH,
    THOUGHTS_TOOL_PARAMETERS,
    current_depth,
    eligible_thoughts,
    extract_json_payload,
    record_expansions,
    record_thought_scores,
    record_thoughts,
    thought_label,
    validate_expansions_payload,
    validate_scores_payload,
    validate_thoughts_payload,
)
from consensus.models import Entity, EntityType


def _entity(eid: int = 1, name: str = "Alice") -> Entity:
    return Entity(id=eid, name=name, entity_type=EntityType.AI)


def _full_scores(feasibility: int = 3, impact: int = 3,
                 risk: int = 3) -> dict:
    return {"feasibility": feasibility, "impact": impact, "risk": risk}


#: Genuinely distinct approach texts (no word-overlap dedup collisions).
_THOUGHT_TEXTS = [
    "Build a lightweight browser extension shipping incrementally",
    "Rewrite the core engine in Rust for raw performance",
    "Partner with an established vendor and resell their platform",
    "Crowdsource the catalogue through community moderation tools",
    "Automate ingestion with scheduled scraping plus manual review",
    "License the dataset commercially to fund ongoing curation",
]


def _state_with_thoughts(n: int = 4) -> dict:
    """State holding n recorded thoughts from one author."""
    state: dict = {}
    record_thoughts(state, _entity(), _THOUGHT_TEXTS[:n])
    assert len(state["thoughts"]) == n
    return state


class TestThoughtLabel:
    def test_label_format(self):
        assert thought_label(3) == "T3"


class TestValidateThoughtsPayload:
    def test_valid_payload_passes(self):
        payload = {"thoughts": ["Build a lightweight browser extension "
                                "instead of a full app"],
                   "reasoning": "One clearly distinct approach."}
        assert validate_thoughts_payload(payload) == ""

    def test_missing_thoughts_rejected(self):
        assert "thoughts" in validate_thoughts_payload(
            {"reasoning": "no list"})

    def test_short_thought_rejected(self):
        error = validate_thoughts_payload(
            {"thoughts": ["too short"], "reasoning": "r"})
        assert str(MIN_THOUGHT_LENGTH) in error

    def test_non_string_thought_rejected(self):
        assert validate_thoughts_payload(
            {"thoughts": [42], "reasoning": "r"}) != ""

    def test_blank_reasoning_rejected(self):
        error = validate_thoughts_payload(
            {"thoughts": ["A perfectly long and valid approach text"],
             "reasoning": "  "})
        assert "reasoning" in error


class TestRecordThoughts:
    def test_records_with_sequential_ids_and_strips(self):
        state: dict = {}
        accepted = record_thoughts(state, _entity(), [
            "  Build a plugin marketplace for third parties.  ",
            "Ship a hosted SaaS version with usage billing",
        ])
        assert [t["id"] for t in accepted] == [1, 2]
        assert accepted[0]["text"] == "Build a plugin marketplace for third parties"
        assert state["thoughts"] == accepted

    def test_dedups_word_overlap_across_entities(self):
        state: dict = {}
        record_thoughts(state, _entity(1, "Alice"),
                        ["Build a plugin marketplace for third parties"])
        accepted = record_thoughts(state, _entity(2, "Bob"), [
            "Build a plugin marketplace for the third parties",
            "Rewrite the core engine in Rust for performance",
        ])
        assert len(accepted) == 1
        assert accepted[0]["text"].startswith("Rewrite the core engine")
        assert [t["id"] for t in state["thoughts"]] == [1, 2]

    def test_rejects_short_thoughts(self):
        state: dict = {}
        assert record_thoughts(state, _entity(), ["tiny"]) == []
        assert state["thoughts"] == []


class TestValidateScoresPayload:
    def test_valid_payload_passes(self):
        state = _state_with_thoughts(2)
        payload = {"scores": {"T1": _full_scores(4, 5, 2),
                              "T2": _full_scores(2, 3, 4)},
                   "reasoning": "T1 is stronger on impact."}
        assert validate_scores_payload(
            payload, eligible_thoughts(state)) == ""

    def test_unknown_label_names_valid_set(self):
        state = _state_with_thoughts(2)
        error = validate_scores_payload(
            {"scores": {"T9": _full_scores()}, "reasoning": "r"},
            eligible_thoughts(state))
        assert "T9" in error and "T1" in error

    def test_missing_dimension_rejected(self):
        state = _state_with_thoughts(1)
        error = validate_scores_payload(
            {"scores": {"T1": {"feasibility": 3, "impact": 3}},
             "reasoning": "r"},
            eligible_thoughts(state))
        assert "risk" in error

    def test_bool_and_out_of_range_rejected(self):
        state = _state_with_thoughts(1)
        bool_err = validate_scores_payload(
            {"scores": {"T1": {"feasibility": True, "impact": 3,
                               "risk": 3}}, "reasoning": "r"},
            eligible_thoughts(state))
        assert bool_err != ""
        range_err = validate_scores_payload(
            {"scores": {"T1": _full_scores(feasibility=SCORE_MAX + 1)},
             "reasoning": "r"},
            eligible_thoughts(state))
        assert str(SCORE_MAX) in range_err

    def test_empty_scores_rejected(self):
        state = _state_with_thoughts(1)
        assert validate_scores_payload(
            {"scores": {}, "reasoning": "r"},
            eligible_thoughts(state)) != ""

    def test_blank_reasoning_rejected(self):
        state = _state_with_thoughts(1)
        error = validate_scores_payload(
            {"scores": {"T1": _full_scores()}, "reasoning": ""},
            eligible_thoughts(state))
        assert "reasoning" in error


class TestRecordThoughtScores:
    def test_records_cells_for_entity(self):
        state = _state_with_thoughts(2)
        kept = record_thought_scores(
            state, _entity(7, "Bob"),
            {"T1": _full_scores(4, 5, 2), "T2": _full_scores(1, 2, 5)})
        assert kept == 2
        assert state["thought_scores"]["7"]["T1"] == _full_scores(4, 5, 2)

    def test_merges_per_thought_not_wholesale(self):
        state = _state_with_thoughts(2)
        entity = _entity(7, "Bob")
        record_thought_scores(state, entity,
                              {"T1": _full_scores(4, 5, 2),
                               "T2": _full_scores(1, 2, 5)})
        record_thought_scores(state, entity, {"T2": _full_scores(3, 3, 3)})
        assert state["thought_scores"]["7"]["T1"] == _full_scores(4, 5, 2)
        assert state["thought_scores"]["7"]["T2"] == _full_scores(3, 3, 3)

    def test_coerces_numeric_strings_and_drops_junk(self):
        state = _state_with_thoughts(2)
        kept = record_thought_scores(state, _entity(), {
            "T1": {"feasibility": "4", "impact": 5, "risk": 2},
            "T2": {"feasibility": True, "impact": "junk", "risk": 9},
            "T9": _full_scores(),
        })
        assert kept == 1
        assert state["thought_scores"]["1"]["T1"] == _full_scores(4, 5, 2)
        assert "T2" not in state["thought_scores"]["1"]

    def test_incomplete_entry_dropped(self):
        state = _state_with_thoughts(1)
        kept = record_thought_scores(
            state, _entity(), {"T1": {"feasibility": 4, "impact": 5}})
        assert kept == 0
        assert state.get("thought_scores", {}) == {}


class TestCompositeAndBeam:
    def test_composite_inverts_risk(self):
        assert composite_of(_full_scores(5, 5, 1)) == 15
        assert composite_of(_full_scores(1, 1, 5)) == 3
        assert composite_of(_full_scores(3, 3, 3)) == 9

    def test_ranking_means_over_scorers_with_tiebreak_by_id(self):
        state = _state_with_thoughts(2)
        record_thought_scores(state, _entity(1, "Alice"),
                              {"T1": _full_scores(5, 5, 1),
                               "T2": _full_scores(5, 5, 1)})
        record_thought_scores(state, _entity(2, "Bob"),
                              {"T1": _full_scores(3, 3, 3),
                               "T2": _full_scores(3, 3, 3)})
        composites = thought_composites(state)
        assert composites[1]["composite"] == 12.0  # mean of 15 and 9
        assert composites[1]["scorer_count"] == 2
        _, ranking = compute_beam(state)
        assert [r["id"] for r in ranking] == [1, 2]  # tie → lower id first

    def test_unscored_thought_defaults_to_midpoint_composite(self):
        state = _state_with_thoughts(2)
        record_thought_scores(state, _entity(),
                              {"T1": _full_scores(5, 5, 1)})
        composites = thought_composites(state)
        assert composites[2]["composite"] == float(
            composite_of({d: DEFAULT_DIMENSION_SCORE for d in DIMENSIONS}))
        assert composites[2]["scorer_count"] == 0

    def test_beam_is_top_beam_width(self):
        state = _state_with_thoughts(BEAM_WIDTH + 2)
        scores = {thought_label(t["id"]):
                  _full_scores(feasibility=min(t["id"], SCORE_MAX))
                  for t in state["thoughts"]}
        record_thought_scores(state, _entity(), scores)
        beam_ids, ranking = compute_beam(state)
        assert len(beam_ids) == BEAM_WIDTH
        assert beam_ids[0] == BEAM_WIDTH + 2  # highest feasibility wins
        assert len(ranking) == BEAM_WIDTH + 2

    def test_eligible_thoughts_all_then_latest_beam(self):
        state = _state_with_thoughts(4)
        assert [t["id"] for t in eligible_thoughts(state)] == [1, 2, 3, 4]
        state["beam_history"] = [
            {"depth": 1, "beam_ids": [2, 3], "ranking": []}]
        assert [t["id"] for t in eligible_thoughts(state)] == [2, 3]

    def test_current_depth_counts_prune_passes(self):
        state = _state_with_thoughts(2)
        assert current_depth(state) == 0
        state["beam_history"] = [
            {"depth": 1, "beam_ids": [1, 2], "ranking": []}]
        assert current_depth(state) == 1


class TestValidateExpansionsPayload:
    def test_valid_payload_passes(self):
        payload = {"expansions": [
            {"thought_id": 2,
             "refinement": "Concretise the rollout as a three-step pilot",
             "obstacles": ["Requires early customer buy-in"]}],
            "reasoning": "Focused on the strongest survivor."}
        assert validate_expansions_payload(payload, {1, 2}) == ""

    def test_id_outside_beam_rejected(self):
        error = validate_expansions_payload(
            {"expansions": [
                {"thought_id": 9,
                 "refinement": "A long enough refinement text here"}],
             "reasoning": "r"}, {1, 2})
        assert "9" in error and "1" in error

    def test_short_refinement_rejected(self):
        error = validate_expansions_payload(
            {"expansions": [{"thought_id": 1, "refinement": "meh"}],
             "reasoning": "r"}, {1, 2})
        assert str(MIN_REFINEMENT_LENGTH) in error

    def test_non_string_obstacles_rejected(self):
        error = validate_expansions_payload(
            {"expansions": [
                {"thought_id": 1,
                 "refinement": "A long enough refinement text here",
                 "obstacles": [42]}],
             "reasoning": "r"}, {1, 2})
        assert "obstacles" in error

    def test_blank_reasoning_rejected(self):
        error = validate_expansions_payload(
            {"expansions": [
                {"thought_id": 1,
                 "refinement": "A long enough refinement text here"}],
             "reasoning": " "}, {1, 2})
        assert "reasoning" in error


class TestRecordExpansions:
    def test_records_with_depth_tag(self):
        state = _state_with_thoughts(2)
        accepted = record_expansions(state, _entity(3, "Cara"), [
            {"thought_id": 1,
             "refinement": "Pilot with three design partners first",
             "obstacles": ["Partner recruitment", 42]}], depth=2)
        assert accepted == 1
        exp = state["expansions"][0]
        assert exp["depth"] == 2 and exp["thought_id"] == 1
        assert exp["entity_name"] == "Cara"
        assert exp["obstacles"] == ["Partner recruitment", "42"]

    def test_skips_unknown_ids_and_short_refinements(self):
        state = _state_with_thoughts(1)
        accepted = record_expansions(state, _entity(), [
            {"thought_id": 9,
             "refinement": "Long enough but the id is unknown"},
            {"thought_id": 1, "refinement": "meh"}], depth=1)
        assert accepted == 0
        assert state["expansions"] == []


class TestArtifact:
    def _scored_state(self) -> dict:
        state = _state_with_thoughts(3)
        record_thought_scores(state, _entity(), {
            "T1": _full_scores(5, 5, 1),
            "T2": _full_scores(3, 3, 3),
            "T3": _full_scores(1, 1, 5),
        })
        beam_ids, ranking = compute_beam(state)
        state["beam_history"] = [
            {"depth": 1, "beam_ids": beam_ids, "ranking": ranking}]
        return state

    def test_artifact_shape_and_recommendation(self):
        state = self._scored_state()
        artifact = build_tot_artifact(state, STOP_CONVERGED)
        assert artifact["stop_reason"] == STOP_CONVERGED
        assert artifact["converged"] is True
        assert artifact["depth"] == 1
        assert artifact["recommendation"]["id"] == 1
        assert artifact["recommendation"]["composite"] == 15.0
        assert [b["id"] for b in artifact["final_beam"]] == [1, 2, 3]
        assert artifact["beam_history"] == state["beam_history"]

    def test_caveats_zero_scorers_and_stop_reason(self):
        state = _state_with_thoughts(2)
        beam_ids, ranking = compute_beam(state)
        state["beam_history"] = [
            {"depth": 1, "beam_ids": beam_ids, "ranking": ranking}]
        artifact = build_tot_artifact(state, STOP_DEPTH)
        assert artifact["converged"] is False
        joined = " ".join(artifact["caveats"]).lower()
        assert "scor" in joined  # zero scorers caveat
        assert any(STOP_DEPTH.replace("_", " ") in c.lower()
                   or "depth" in c.lower() for c in artifact["caveats"])

    def test_degenerate_caveat(self):
        state = _state_with_thoughts(1)
        beam_ids, ranking = compute_beam(state)
        state["beam_history"] = [
            {"depth": 1, "beam_ids": beam_ids, "ranking": ranking}]
        artifact = build_tot_artifact(state, STOP_DEGENERATE)
        assert artifact["stop_reason"] == STOP_DEGENERATE
        assert artifact["converged"] is False


class TestExtractJsonPayload:
    def test_fenced_json_block(self):
        content = ('Here you go:\n```json\n'
                   '{"scores": {"T1": {"feasibility": 4, "impact": 5, '
                   '"risk": 2}}, "reasoning": "ok"}\n```')
        data = extract_json_payload(content, "scores")
        assert data == {"T1": {"feasibility": 4, "impact": 5, "risk": 2}}

    def test_inline_balanced_braces(self):
        content = ('My scores are {"scores": {"T1": {"feasibility": 4, '
                   '"impact": 5, "risk": 2}}} as discussed.')
        data = extract_json_payload(content, "scores")
        assert data == {"T1": {"feasibility": 4, "impact": 5, "risk": 2}}

    def test_array_payload(self):
        content = ('```json\n{"expansions": [{"thought_id": 1, '
                   '"refinement": "Make it a staged pilot rollout"}]}\n```')
        data = extract_json_payload(content, "expansions")
        assert isinstance(data, list) and data[0]["thought_id"] == 1

    def test_no_payload_returns_none(self):
        assert extract_json_payload("no json here", "scores") is None


class TestFormatting:
    def test_format_thoughts_lists_labels(self):
        state = _state_with_thoughts(2)
        text = format_thoughts(state["thoughts"])
        assert "T1" in text and "T2" in text

    def test_format_ranking_and_beam(self):
        state = _state_with_thoughts(2)
        record_thought_scores(state, _entity(),
                              {"T1": _full_scores(5, 5, 1)})
        assert "T1" in format_ranking(state)
        state["beam_history"] = [
            {"depth": 1, "beam_ids": [1],
             "ranking": [{"id": 1, "composite": 14.0, "scorer_count": 1}]}]
        assert "T1" in format_beam(state)

    def test_format_expansions_filters_by_depth(self):
        state = _state_with_thoughts(1)
        record_expansions(state, _entity(), [
            {"thought_id": 1,
             "refinement": "Depth-one refinement of the approach"}],
            depth=1)
        assert "Depth-one" in format_expansions(state, 1)
        assert format_expansions(state, 2) == "  (No expansions)"

    def test_format_beam_trajectory(self):
        state = _state_with_thoughts(1)
        state["beam_history"] = [
            {"depth": 1, "beam_ids": [1],
             "ranking": [{"id": 1, "composite": 9.0, "scorer_count": 0}]}]
        assert "Pass 1" in format_beam_trajectory(state)


class TestConstants:
    def test_schema_shapes(self):
        assert THOUGHTS_TOOL_PARAMETERS["required"] == ["thoughts",
                                                        "reasoning"]
        score_entry = SCORES_TOOL_PARAMETERS["properties"]["scores"][
            "additionalProperties"]
        assert set(score_entry["required"]) == set(DIMENSIONS)
        for dim in DIMENSIONS:
            assert score_entry["properties"][dim]["minimum"] == SCORE_MIN
            assert score_entry["properties"][dim]["maximum"] == SCORE_MAX
        exp_items = EXPANSIONS_TOOL_PARAMETERS["properties"]["expansions"][
            "items"]
        assert exp_items["required"] == ["thought_id", "refinement"]

    def test_bounds_sane(self):
        assert MIN_BEAM_SIZE == 2
        assert 2 <= BEAM_WIDTH <= 3
        assert MAX_TOT_DEPTH >= 2
        assert MAX_PROPOSE_ROUNDS >= 2
