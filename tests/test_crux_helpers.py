"""Tests for Double Crux shared helpers (issue #27).

Pure-function coverage: payload validators, crux recording with
per-entity word-overlap dedupe, crux-selection recording (verdict +
initial-belief snapshot), resolution recording/replacement, and
free-text extraction fallbacks.  The crux_map artifact and display
formatting are covered in ``test_crux_artifact.py``.
"""

from consensus.methods.phases._crux_helpers import (
    CRUX_SELECTION_TOOL_PARAMETERS,
    CRUXES_TOOL_PARAMETERS,
    MAX_CRUX_SEARCH_ROUNDS,
    MAX_CRUXES_PER_ENTITY,
    MAX_HUNT_ROUNDS,
    MAX_IDENTIFY_ATTEMPTS,
    MAX_POLL_ROUNDS,
    MAX_RESOLVE_ROUNDS,
    MIN_CLAIM_LENGTH,
    POLL_BELIEF_TOOL_PARAMETERS,
    RESOLUTION_TOOL_PARAMETERS,
    TEST_CRUX_ROUNDS,
    VERDICT_FACTUAL,
    VERDICT_NONE,
    VERDICT_VALUES,
    apply_poll_beliefs,
    entities_with_poll,
    entities_with_resolutions,
    extract_crux_selection,
    extract_cruxes,
    extract_poll_belief,
    extract_resolution,
    record_crux_selection,
    record_cruxes,
    record_poll_belief,
    record_resolution,
    validate_crux_selection_payload,
    validate_cruxes_payload,
    validate_poll_belief_payload,
    validate_resolution_payload,
)
from consensus.models import Entity, EntityType


def _entity(eid: int = 1, name: str = "Alice") -> Entity:
    return Entity(id=eid, name=name, entity_type=EntityType.AI)


CLAIM_A = "Remote work reduces measured team productivity"
CLAIM_B = "Office presence improves informal knowledge transfer"


def _crux(claim: str = CLAIM_A, belief: float = 0.8) -> dict:
    return {"claim": claim, "belief": belief,
            "why_pivotal": "My whole position rests on this."}


class TestConstants:
    def test_caps_are_positive(self):
        assert MIN_CLAIM_LENGTH > 0
        assert MAX_HUNT_ROUNDS > 0
        assert MAX_CRUX_SEARCH_ROUNDS > 0
        assert MAX_IDENTIFY_ATTEMPTS > 0
        assert MAX_RESOLVE_ROUNDS > 0
        assert TEST_CRUX_ROUNDS > 0

    def test_schemas_require_reasoning(self):
        assert "reasoning" in CRUXES_TOOL_PARAMETERS["required"]
        assert "reasoning" in CRUX_SELECTION_TOOL_PARAMETERS["required"]
        assert "reasoning" in RESOLUTION_TOOL_PARAMETERS["required"]

    def test_verdicts_match_schema_enum(self):
        enum = CRUX_SELECTION_TOOL_PARAMETERS["properties"]["verdict"]["enum"]
        assert sorted(enum) == sorted(
            [VERDICT_FACTUAL, VERDICT_VALUES, VERDICT_NONE])


class TestValidateCruxesPayload:
    def test_valid(self):
        payload = {"cruxes": [_crux()], "reasoning": "Traced my position."}
        assert validate_cruxes_payload(payload) == ""

    def test_empty_list_rejected(self):
        assert validate_cruxes_payload(
            {"cruxes": [], "reasoning": "x"}) != ""

    def test_too_many_cruxes_rejected(self):
        # The schema's maxItems is only advisory to the model; the
        # validator enforces the cap server-side with a retry message.
        cruxes = [_crux(f"{CLAIM_A} variant number {n}")
                  for n in range(MAX_CRUXES_PER_ENTITY + 1)]
        assert validate_cruxes_payload(
            {"cruxes": cruxes, "reasoning": "Traced my position."}) != ""

    def test_schema_max_items_uses_the_constant(self):
        assert (CRUXES_TOOL_PARAMETERS["properties"]["cruxes"]["maxItems"]
                == MAX_CRUXES_PER_ENTITY)

    def test_short_claim_rejected(self):
        payload = {"cruxes": [_crux(claim="Too short")],
                   "reasoning": "Traced my position."}
        assert validate_cruxes_payload(payload) != ""

    def test_belief_out_of_range_rejected(self):
        payload = {"cruxes": [_crux(belief=1.5)],
                   "reasoning": "Traced my position."}
        assert validate_cruxes_payload(payload) != ""

    def test_boolean_belief_rejected(self):
        payload = {"cruxes": [_crux(belief=True)],
                   "reasoning": "Traced my position."}
        assert validate_cruxes_payload(payload) != ""

    def test_missing_why_pivotal_rejected(self):
        crux = _crux()
        crux["why_pivotal"] = ""
        assert validate_cruxes_payload(
            {"cruxes": [crux], "reasoning": "x" * 20}) != ""

    def test_missing_reasoning_rejected(self):
        assert validate_cruxes_payload({"cruxes": [_crux()]}) != ""
        assert validate_cruxes_payload(
            {"cruxes": [_crux()], "reasoning": None}) != ""


class TestRecordCruxes:
    def test_records_with_sequential_ids(self):
        state: dict = {}
        accepted = record_cruxes(state, _entity(), [_crux(CLAIM_A),
                                                    _crux(CLAIM_B, 0.3)])
        assert [c["id"] for c in state["cruxes"]] == [1, 2]
        assert accepted == state["cruxes"]
        assert state["cruxes"][0]["entity_name"] == "Alice"
        assert state["cruxes"][1]["belief"] == 0.3

    def test_same_entity_duplicates_dropped(self):
        state: dict = {}
        record_cruxes(state, _entity(), [_crux(CLAIM_A)])
        accepted = record_cruxes(state, _entity(),
                                 [_crux(CLAIM_A + " overall")])
        assert accepted == []
        assert len(state["cruxes"]) == 1

    def test_cross_entity_similar_claims_kept(self):
        # Overlap between different entities is the shared-crux signal.
        state: dict = {}
        record_cruxes(state, _entity(1, "Alice"), [_crux(CLAIM_A)])
        accepted = record_cruxes(state, _entity(2, "Bob"),
                                 [_crux(CLAIM_A + " overall", 0.2)])
        assert len(accepted) == 1
        assert len(state["cruxes"]) == 2

    def test_short_claims_skipped(self):
        state: dict = {}
        accepted = record_cruxes(state, _entity(), [_crux("Nope")])
        assert accepted == []
        assert state["cruxes"] == []

    def test_claim_normalised(self):
        state: dict = {}
        record_cruxes(state, _entity(), [_crux("  " + CLAIM_A + ".  ")])
        assert state["cruxes"][0]["claim"] == CLAIM_A

    def test_per_turn_bound_applies_to_free_text_path(self):
        # record_cruxes is the free-text path's only gate, so it must
        # honour the same per-turn cap the schema puts on the tool path.
        # Claims share no words, so the dedupe never fires and only the
        # cap limits what gets recorded.
        distinct_claims = [
            "Remote work reduces measured team productivity",
            "Office presence improves informal knowledge transfer",
            "Commuting time damages employee wellbeing",
            "Hybrid schedules complicate meeting coordination",
            "Junior staff onboard faster with mentors nearby",
            "Real estate savings outweigh collaboration losses",
            "Asynchronous writing sharpens decision quality",
            "Timezone spread widens the hiring pool",
        ]
        assert len(distinct_claims) > MAX_CRUXES_PER_ENTITY
        state: dict = {}
        accepted = record_cruxes(state, _entity(),
                                 [_crux(c) for c in distinct_claims])
        assert len(accepted) == MAX_CRUXES_PER_ENTITY
        assert len(state["cruxes"]) == MAX_CRUXES_PER_ENTITY

    def test_belief_clamped_and_none_allowed(self):
        state: dict = {}
        record_cruxes(state, _entity(), [
            {"claim": CLAIM_A, "belief": 1.7, "why_pivotal": "w"},
            {"claim": CLAIM_B, "belief": None, "why_pivotal": ""},
        ])
        assert state["cruxes"][0]["belief"] == 1.0
        assert state["cruxes"][1]["belief"] is None


class TestExtractCruxes:
    def test_json_block(self):
        content = ('Here you go:\n```json\n{"cruxes": [{"claim": "'
                   + CLAIM_A + '", "belief": 0.9, "why_pivotal": "core"}]}'
                   "\n```")
        items = extract_cruxes(content)
        assert items[0]["claim"] == CLAIM_A
        assert items[0]["belief"] == 0.9

    def test_numbered_list_fallback(self):
        items = extract_cruxes(f"1. {CLAIM_A}\n2. {CLAIM_B}")
        assert [i["claim"] for i in items] == [CLAIM_A, CLAIM_B]
        assert all(i["belief"] is None for i in items)

    def test_nothing_found(self):
        assert extract_cruxes("I am not sure what to say.") == []


class TestValidateCruxSelection:
    def _state_ids(self) -> set[int]:
        return {1, 2}

    def test_factual_valid(self):
        payload = {"verdict": "factual", "crux_ids": [1, 2],
                   "claim": CLAIM_A, "reasoning": "Both parties named it."}
        assert validate_crux_selection_payload(
            payload, self._state_ids()) == ""

    def test_factual_requires_crux_ids(self):
        payload = {"verdict": "factual", "claim": CLAIM_A,
                   "reasoning": "x" * 20}
        assert validate_crux_selection_payload(
            payload, self._state_ids()) != ""

    def test_factual_rejects_unknown_ids(self):
        payload = {"verdict": "factual", "crux_ids": [7],
                   "claim": CLAIM_A, "reasoning": "x" * 20}
        assert validate_crux_selection_payload(
            payload, self._state_ids()) != ""

    def test_factual_requires_claim(self):
        payload = {"verdict": "factual", "crux_ids": [1],
                   "reasoning": "x" * 20}
        assert validate_crux_selection_payload(
            payload, self._state_ids()) != ""

    def test_values_requires_claim(self):
        assert validate_crux_selection_payload(
            {"verdict": "values", "reasoning": "x" * 20},
            self._state_ids()) != ""
        assert validate_crux_selection_payload(
            {"verdict": "values", "claim": "Liberty matters more than GDP",
             "reasoning": "x" * 20}, self._state_ids()) == ""

    def test_none_needs_nothing_extra(self):
        assert validate_crux_selection_payload(
            {"verdict": "none", "reasoning": "No overlap yet."},
            self._state_ids()) == ""

    def test_unknown_verdict_rejected(self):
        assert validate_crux_selection_payload(
            {"verdict": "maybe", "reasoning": "x" * 20},
            self._state_ids()) != ""

    def test_missing_reasoning_rejected(self):
        assert validate_crux_selection_payload(
            {"verdict": "none"}, self._state_ids()) != ""


class TestRecordCruxSelection:
    def _hunted_state(self) -> dict:
        state: dict = {}
        record_cruxes(state, _entity(1, "Alice"), [_crux(CLAIM_A, 0.9)])
        record_cruxes(state, _entity(2, "Bob"), [_crux(CLAIM_B, 0.4),
                                                 _crux(CLAIM_A + " overall",
                                                       0.2)])
        return state

    def test_factual_snapshots_initial_beliefs(self):
        state = self._hunted_state()
        record_crux_selection(state, {
            "verdict": "factual", "crux_ids": [1, 3], "claim": CLAIM_A,
            "reasoning": "Both named it."})
        assert state["crux_verdict"] == VERDICT_FACTUAL
        shared = state["shared_crux"]
        assert shared["claim"] == CLAIM_A
        assert shared["source_crux_ids"] == [1, 3]
        assert shared["initial_beliefs"] == {"Alice": 0.9, "Bob": 0.2}

    def test_factual_skips_none_beliefs(self):
        state: dict = {}
        record_cruxes(state, _entity(1, "Alice"),
                      [{"claim": CLAIM_A, "belief": None, "why_pivotal": ""}])
        record_crux_selection(state, {
            "verdict": "factual", "crux_ids": [1], "claim": CLAIM_A,
            "reasoning": "r"})
        assert state["shared_crux"]["initial_beliefs"] == {}

    def test_values_stores_description(self):
        state = self._hunted_state()
        record_crux_selection(state, {
            "verdict": "values", "claim": "Liberty matters more than GDP",
            "reasoning": "Positions differ on priorities."})
        assert state["crux_verdict"] == VERDICT_VALUES
        assert (state["shared_crux"]["description"]
                == "Liberty matters more than GDP")

    def test_none_clears_shared_crux(self):
        state = self._hunted_state()
        record_crux_selection(state, {"verdict": "none", "reasoning": "r"})
        assert state["crux_verdict"] == VERDICT_NONE
        assert state["shared_crux"] == {}


class TestExtractCruxSelection:
    def test_json_block(self):
        content = ('```json\n{"verdict": "none", "reasoning": "nothing"}\n```')
        assert extract_crux_selection(content)["verdict"] == "none"

    def test_no_verdict_key(self):
        assert extract_crux_selection('```json\n{"foo": 1}\n```') is None

    def test_plain_text(self):
        assert extract_crux_selection("No crux emerged.") is None


class TestValidateResolution:
    def _payload(self, **over) -> dict:
        payload = {"stance": "updated",
                   "position": "I now think hybrid work is best",
                   "crux_belief": 0.4, "reasoning": "The trials moved me."}
        payload.update(over)
        return payload

    def test_valid_with_belief(self):
        assert validate_resolution_payload(
            self._payload(), require_belief=True) == ""

    def test_belief_required_when_factual(self):
        payload = self._payload()
        del payload["crux_belief"]
        assert validate_resolution_payload(payload, require_belief=True) != ""
        assert validate_resolution_payload(
            self._payload(crux_belief=None), require_belief=True) != ""

    def test_belief_optional_otherwise(self):
        payload = self._payload()
        del payload["crux_belief"]
        assert validate_resolution_payload(payload,
                                           require_belief=False) == ""

    def test_supplied_belief_still_range_checked(self):
        assert validate_resolution_payload(
            self._payload(crux_belief=2.0), require_belief=False) != ""
        assert validate_resolution_payload(
            self._payload(crux_belief=True), require_belief=False) != ""

    def test_bad_stance_rejected(self):
        assert validate_resolution_payload(
            self._payload(stance="waffling"), require_belief=True) != ""

    def test_short_position_rejected(self):
        assert validate_resolution_payload(
            self._payload(position="ok"), require_belief=True) != ""

    def test_missing_reasoning_rejected(self):
        assert validate_resolution_payload(
            self._payload(reasoning=""), require_belief=True) != ""


class TestRecordResolution:
    def test_records_and_replaces_own(self):
        state: dict = {}
        record_resolution(state, _entity(1, "Alice"), {
            "stance": "unchanged", "position": "Remote is fine",
            "crux_belief": 0.8, "reasoning": "r"})
        record_resolution(state, _entity(2, "Bob"), {
            "stance": "updated", "position": "Hybrid after all",
            "crux_belief": 0.5, "reasoning": "r"})
        record_resolution(state, _entity(1, "Alice"), {
            "stance": "updated", "position": "Hybrid it is",
            "crux_belief": 0.45, "reasoning": "r"})
        assert len(state["resolutions"]) == 2
        alice = next(r for r in state["resolutions"]
                     if r["entity_id"] == 1)
        assert alice["stance"] == "updated"
        assert alice["crux_belief"] == 0.45
        assert entities_with_resolutions(state) == {1, 2}

    def test_missing_belief_stored_as_none(self):
        state: dict = {}
        record_resolution(state, _entity(), {
            "stance": "unchanged", "position": "Values differ, that is all",
            "reasoning": "r"})
        assert state["resolutions"][0]["crux_belief"] is None


class TestExtractResolution:
    def test_json_block(self):
        content = ('```json\n{"stance": "updated", "position": "'
                   "Hybrid work wins overall"
                   '", "reasoning": "evidence"}\n```')
        assert extract_resolution(content)["stance"] == "updated"

    def test_plain_text(self):
        assert extract_resolution("I still feel the same.") is None


class TestValidatePollBelief:
    def test_accepts_valid(self):
        assert validate_poll_belief_payload(
            {"belief": 0.6, "reasoning": "prior evidence leans this way"}) == ""

    def test_rejects_out_of_range_belief(self):
        assert validate_poll_belief_payload(
            {"belief": 1.5, "reasoning": "r"}) != ""

    def test_rejects_non_numeric_belief(self):
        assert validate_poll_belief_payload(
            {"belief": "high", "reasoning": "r"}) != ""

    def test_rejects_boolean_belief(self):
        assert validate_poll_belief_payload(
            {"belief": True, "reasoning": "r"}) != ""

    def test_requires_reasoning(self):
        assert validate_poll_belief_payload({"belief": 0.5}) != ""
        assert validate_poll_belief_payload(
            {"belief": 0.5, "reasoning": "  "}) != ""


class TestRecordPollBelief:
    def test_appends_entry(self):
        state: dict = {}
        record_poll_belief(state, _entity(1, "Alice"),
                           {"belief": 0.7, "reasoning": "prior data"})
        assert state["poll_beliefs"] == [{
            "entity_id": 1, "entity_name": "Alice",
            "belief": 0.7, "reasoning": "prior data"}]

    def test_replaces_own_entry(self):
        state: dict = {}
        record_poll_belief(state, _entity(1, "Alice"),
                           {"belief": 0.7, "reasoning": "first"})
        record_poll_belief(state, _entity(1, "Alice"),
                           {"belief": 0.3, "reasoning": "revised"})
        assert len(state["poll_beliefs"]) == 1
        assert state["poll_beliefs"][0]["belief"] == 0.3

    def test_coerces_belief_to_float(self):
        state: dict = {}
        record_poll_belief(state, _entity(1, "Alice"),
                           {"belief": 1, "reasoning": "certain"})
        assert state["poll_beliefs"][0]["belief"] == 1.0


class TestEntitiesWithPoll:
    def test_returns_polled_ids(self):
        state: dict = {}
        record_poll_belief(state, _entity(1, "Alice"),
                           {"belief": 0.7, "reasoning": "r"})
        record_poll_belief(state, _entity(2, "Bob"),
                           {"belief": 0.2, "reasoning": "r"})
        assert entities_with_poll(state) == {1, 2}

    def test_empty_when_none(self):
        assert entities_with_poll({}) == set()


class TestExtractPollBelief:
    def test_reads_json_block(self):
        content = '```json\n{"belief": 0.4, "reasoning": "r"}\n```'
        assert extract_poll_belief(content) == {"belief": 0.4,
                                                "reasoning": "r"}

    def test_none_without_belief_key(self):
        assert extract_poll_belief("I am about 40% sure.") is None
        assert extract_poll_belief('```json\n{"reasoning": "r"}\n```') is None


class TestApplyPollBeliefs:
    def test_folds_into_initial_beliefs(self):
        state: dict = {"shared_crux": {"claim": "c", "initial_beliefs": {}}}
        record_poll_belief(state, _entity(1, "Alice"),
                           {"belief": 0.7, "reasoning": "r"})
        record_poll_belief(state, _entity(2, "Bob"),
                           {"belief": 0.2, "reasoning": "r"})
        apply_poll_beliefs(state)
        assert state["shared_crux"]["initial_beliefs"] == {
            "Alice": 0.7, "Bob": 0.2}

    def test_replaces_prior_initial_beliefs(self):
        # Any snapshot value present is overwritten by the poll (replace,
        # not merge) — the poll is authoritative.
        state: dict = {"shared_crux": {
            "claim": "c", "initial_beliefs": {"Alice": 0.99}}}
        record_poll_belief(state, _entity(1, "Alice"),
                           {"belief": 0.7, "reasoning": "r"})
        apply_poll_beliefs(state)
        assert state["shared_crux"]["initial_beliefs"] == {"Alice": 0.7}

    def test_empty_poll_yields_empty_beliefs(self):
        state: dict = {"shared_crux": {"claim": "c", "initial_beliefs": {}}}
        apply_poll_beliefs(state)
        assert state["shared_crux"]["initial_beliefs"] == {}
