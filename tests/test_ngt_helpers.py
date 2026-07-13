"""Tests for Nominal Group Technique shared helpers (issue #24).

Pure-function coverage: payload validators, idea recording with
word-overlap dedup, candidate management (including the clustering
give-up fallback), point-allocation validation/recording, free-text
allocation extraction, tallying, and display formatting.
"""

from consensus.methods.phases._ngt_helpers import (
    ALLOCATIONS_TOOL_PARAMETERS,
    CANDIDATES_TOOL_PARAMETERS,
    IDEAS_TOOL_PARAMETERS,
    MAX_ALLOCATE_ROUNDS,
    MAX_CLUSTER_ATTEMPTS,
    MAX_GENERATE_ROUNDS,
    MIN_IDEA_LENGTH,
    POINTS_PER_VOTER,
    entities_with_allocations,
    extract_allocations,
    fallback_candidates_from_ideas,
    format_candidates,
    format_ideas_for_clustering,
    format_ranked_candidates,
    record_allocations,
    record_candidates,
    record_ideas,
    tally_points,
    validate_allocations_payload,
    validate_candidates_payload,
    validate_ideas_payload,
)
from consensus.models import Entity, EntityType


def _entity(eid: int = 1, name: str = "Alice") -> Entity:
    return Entity(id=eid, name=name, entity_type=EntityType.AI)


IDEAS_PAYLOAD = {
    "ideas": [
        "Offer a self-serve onboarding checklist inside the product",
        "Run monthly live office hours for new customers",
    ],
    "reasoning": "Covers both self-serve and high-touch onboarding needs.",
}

CANDIDATES_PAYLOAD = {
    "candidates": [
        {"title": "Build a self-serve onboarding checklist",
         "summary": "Merges ideas 1 and 3"},
        {"title": "Run recurring live office hours for customers"},
    ],
    "reasoning": "Merged duplicate self-serve ideas; kept live support separate.",
}


def _state_with_candidates() -> dict:
    state: dict = {}
    record_candidates(state, CANDIDATES_PAYLOAD["candidates"])
    return state


class TestConstants:
    def test_give_up_caps_are_positive(self):
        assert MAX_GENERATE_ROUNDS > 0
        assert MAX_CLUSTER_ATTEMPTS > 0
        assert MAX_ALLOCATE_ROUNDS > 0

    def test_points_pool_positive(self):
        assert POINTS_PER_VOTER > 0


class TestIdeasSchemaAndValidator:
    def test_schema_shape(self):
        assert IDEAS_TOOL_PARAMETERS["type"] == "object"
        assert set(IDEAS_TOOL_PARAMETERS["required"]) == {"ideas", "reasoning"}
        assert IDEAS_TOOL_PARAMETERS["properties"]["ideas"]["type"] == "array"

    def test_valid(self):
        assert validate_ideas_payload(IDEAS_PAYLOAD) == ""

    def test_missing_ideas_rejected(self):
        assert validate_ideas_payload({"reasoning": "x"}) != ""

    def test_ideas_not_a_list_rejected(self):
        assert validate_ideas_payload(
            {"ideas": "one string", "reasoning": "x"}) != ""

    def test_empty_ideas_rejected(self):
        assert validate_ideas_payload({"ideas": [], "reasoning": "x"}) != ""

    def test_short_idea_rejected(self):
        bad = {"ideas": ["x" * (MIN_IDEA_LENGTH - 1)], "reasoning": "x"}
        assert validate_ideas_payload(bad) != ""

    def test_non_string_idea_rejected(self):
        assert validate_ideas_payload(
            {"ideas": [12345678901], "reasoning": "x"}) != ""

    def test_null_reasoning_rejected(self):
        """JSON null must not slip through as the string 'None'."""
        bad = {"ideas": IDEAS_PAYLOAD["ideas"], "reasoning": None}
        assert validate_ideas_payload(bad) != ""

    def test_missing_reasoning_rejected(self):
        assert validate_ideas_payload({"ideas": IDEAS_PAYLOAD["ideas"]}) != ""


class TestRecordIdeas:
    def test_assigns_sequential_ids_and_attribution(self):
        state: dict = {}
        accepted = record_ideas(state, _entity(), IDEAS_PAYLOAD["ideas"])
        assert [i["id"] for i in state["ideas"]] == [1, 2]
        assert accepted == state["ideas"]
        assert state["ideas"][0]["entity_name"] == "Alice"

    def test_dedups_by_word_overlap(self):
        state: dict = {}
        record_ideas(state, _entity(1, "Alice"), IDEAS_PAYLOAD["ideas"])
        accepted = record_ideas(
            state, _entity(2, "Bob"),
            ["Offer a self-serve onboarding checklist inside the product now",
             "Publish a searchable public knowledge base"],
        )
        assert len(accepted) == 1
        assert "knowledge base" in accepted[0]["text"]
        assert len(state["ideas"]) == 3

    def test_strips_trailing_period(self):
        state: dict = {}
        record_ideas(state, _entity(),
                     ["Offer a self-serve onboarding checklist."])
        assert state["ideas"][0]["text"].endswith("checklist")

    def test_drops_short_items(self):
        state: dict = {}
        accepted = record_ideas(state, _entity(), ["Too short"])
        assert accepted == []
        assert state["ideas"] == []


class TestCandidatesSchemaAndValidator:
    def test_schema_shape(self):
        assert set(CANDIDATES_TOOL_PARAMETERS["required"]) == {
            "candidates", "reasoning"}
        items = CANDIDATES_TOOL_PARAMETERS["properties"]["candidates"]["items"]
        assert items["required"] == ["title"]

    def test_valid(self):
        assert validate_candidates_payload(CANDIDATES_PAYLOAD) == ""

    def test_missing_candidates_rejected(self):
        assert validate_candidates_payload({"reasoning": "x"}) != ""

    def test_non_object_candidate_rejected(self):
        bad = {"candidates": ["just a string"], "reasoning": "x"}
        assert validate_candidates_payload(bad) != ""

    def test_short_title_rejected(self):
        bad = {"candidates": [{"title": "Pricing"}], "reasoning": "x"}
        assert validate_candidates_payload(bad) != ""

    def test_non_string_summary_rejected(self):
        bad = {"candidates": [{"title": "A substantive candidate title",
                               "summary": 42}],
               "reasoning": "x"}
        assert validate_candidates_payload(bad) != ""

    def test_null_reasoning_rejected(self):
        bad = {"candidates": CANDIDATES_PAYLOAD["candidates"],
               "reasoning": None}
        assert validate_candidates_payload(bad) != ""


class TestRecordCandidates:
    def test_assigns_sequential_ids(self):
        state = _state_with_candidates()
        assert [c["id"] for c in state["candidates"]] == [1, 2]

    def test_missing_summary_becomes_empty_string(self):
        state = _state_with_candidates()
        assert state["candidates"][1]["summary"] == ""

    def test_replaces_previous_candidates(self):
        state = _state_with_candidates()
        record_candidates(state, [{"title": "A single replacement candidate"}])
        assert len(state["candidates"]) == 1
        assert state["candidates"][0]["id"] == 1

    def test_fallback_promotes_ideas_one_to_one(self):
        state: dict = {}
        record_ideas(state, _entity(), IDEAS_PAYLOAD["ideas"])
        fallback_candidates_from_ideas(state)
        assert len(state["candidates"]) == 2
        assert state["candidates"][0]["title"] == state["ideas"][0]["text"]

    def test_fallback_with_no_ideas_yields_no_candidates(self):
        state: dict = {}
        fallback_candidates_from_ideas(state)
        assert state["candidates"] == []


class TestAllocationsValidator:
    VALID = {
        "allocations": [
            {"candidate_id": 1, "points": 7, "rationale": "Highest leverage"},
            {"candidate_id": 2, "points": 3},
        ],
        "reasoning": "Self-serve scales; office hours still matter.",
    }

    def test_schema_shape(self):
        assert set(ALLOCATIONS_TOOL_PARAMETERS["required"]) == {
            "allocations", "reasoning"}
        items = ALLOCATIONS_TOOL_PARAMETERS["properties"]["allocations"]["items"]
        assert set(items["required"]) == {"candidate_id", "points"}

    def test_valid(self):
        assert validate_allocations_payload(
            self.VALID, {1, 2}, POINTS_PER_VOTER) == ""

    def test_missing_allocations_rejected(self):
        assert validate_allocations_payload(
            {"reasoning": "x"}, {1, 2}, POINTS_PER_VOTER) != ""

    def test_unknown_candidate_rejected(self):
        bad = {"allocations": [{"candidate_id": 9, "points": 10}],
               "reasoning": "x"}
        assert "9" in validate_allocations_payload(bad, {1, 2}, 10)

    def test_duplicate_candidate_rejected(self):
        bad = {"allocations": [{"candidate_id": 1, "points": 5},
                               {"candidate_id": 1, "points": 5}],
               "reasoning": "x"}
        assert validate_allocations_payload(bad, {1, 2}, 10) != ""

    def test_non_integer_points_rejected(self):
        bad = {"allocations": [{"candidate_id": 1, "points": "ten"}],
               "reasoning": "x"}
        assert validate_allocations_payload(bad, {1, 2}, 10) != ""

    def test_boolean_points_rejected(self):
        bad = {"allocations": [{"candidate_id": 1, "points": True}],
               "reasoning": "x"}
        assert validate_allocations_payload(bad, {1, 2}, 10) != ""

    def test_zero_points_rejected(self):
        bad = {"allocations": [{"candidate_id": 1, "points": 0}],
               "reasoning": "x"}
        assert validate_allocations_payload(bad, {1, 2}, 10) != ""

    def test_wrong_sum_rejected(self):
        bad = {"allocations": [{"candidate_id": 1, "points": 4}],
               "reasoning": "x"}
        err = validate_allocations_payload(bad, {1, 2}, 10)
        assert "10" in err

    def test_null_reasoning_rejected(self):
        bad = {"allocations": [{"candidate_id": 1, "points": 10}],
               "reasoning": None}
        assert validate_allocations_payload(bad, {1, 2}, 10) != ""


class TestRecordAllocations:
    def test_records_and_counts(self):
        state = _state_with_candidates()
        n = record_allocations(state, _entity(), [
            {"candidate_id": 1, "points": 7, "rationale": "r1"},
            {"candidate_id": 2, "points": 3},
        ])
        assert n == 2
        assert len(state["point_allocations"]) == 2
        assert state["point_allocations"][0]["entity_name"] == "Alice"
        assert state["point_allocations"][1]["rationale"] == ""

    def test_skips_unknown_candidate(self):
        state = _state_with_candidates()
        n = record_allocations(state, _entity(),
                               [{"candidate_id": 99, "points": 10}])
        assert n == 0

    def test_skips_double_allocation_for_same_candidate(self):
        state = _state_with_candidates()
        record_allocations(state, _entity(),
                           [{"candidate_id": 1, "points": 5}])
        n = record_allocations(state, _entity(),
                               [{"candidate_id": 1, "points": 5}])
        assert n == 0
        assert len(state["point_allocations"]) == 1

    def test_coerces_string_ids_and_points(self):
        state = _state_with_candidates()
        n = record_allocations(state, _entity(),
                               [{"candidate_id": "1", "points": "10"}])
        assert n == 1
        assert state["point_allocations"][0]["points"] == 10

    def test_entities_with_allocations(self):
        state = _state_with_candidates()
        record_allocations(state, _entity(1), [{"candidate_id": 1, "points": 10}])
        record_allocations(state, _entity(2, "Bob"),
                           [{"candidate_id": 2, "points": 10}])
        assert entities_with_allocations(state) == {1, 2}


class TestExtractAllocations:
    def test_extracts_json_block(self):
        content = (
            "Here is my vote:\n```json\n"
            '{"allocations": [{"candidate_id": 1, "points": 6},'
            ' {"candidate_id": 2, "points": 4}]}\n```'
        )
        allocations = extract_allocations(content)
        assert len(allocations) == 2
        assert allocations[0]["candidate_id"] == 1

    def test_extracts_natural_language_lines(self):
        content = "Candidate 1: 6 points\nCandidate 2 - 4 points"
        allocations = extract_allocations(content)
        assert [(a["candidate_id"], a["points"]) for a in allocations] == [
            (1, 6), (2, 4)]

    def test_returns_empty_for_prose(self):
        assert extract_allocations("I like the first idea best.") == []


class TestTallyAndFormatting:
    def _voted_state(self) -> dict:
        state = _state_with_candidates()
        record_allocations(state, _entity(1, "Alice"),
                           [{"candidate_id": 1, "points": 7},
                            {"candidate_id": 2, "points": 3}])
        record_allocations(state, _entity(2, "Bob"),
                           [{"candidate_id": 2, "points": 10}])
        return state

    def test_tally_totals(self):
        totals = tally_points(self._voted_state())
        assert totals == {1: 7, 2: 13}

    def test_tally_includes_zero_point_candidates(self):
        state = _state_with_candidates()
        assert tally_points(state) == {1: 0, 2: 0}

    def test_format_ideas_for_clustering(self):
        state: dict = {}
        record_ideas(state, _entity(), IDEAS_PAYLOAD["ideas"])
        text = format_ideas_for_clustering(state)
        assert "Idea 1:" in text
        assert "onboarding checklist" in text

    def test_format_ideas_empty(self):
        assert "No ideas" in format_ideas_for_clustering({})

    def test_format_candidates_lists_ids_and_summaries(self):
        text = format_candidates(_state_with_candidates())
        assert "Candidate 1:" in text
        assert "Merges ideas 1 and 3" in text

    def test_format_candidates_empty(self):
        assert "No candidates" in format_candidates({})

    def test_format_ranked_orders_by_points(self):
        text = format_ranked_candidates(self._voted_state())
        first_line = text.splitlines()[0]
        assert "office hours" in first_line
        assert "13 point(s)" in first_line
        assert "2 participant(s)" in first_line
