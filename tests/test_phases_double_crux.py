"""Tests for the Double Crux phase handlers (issue #27).

Covers the parametrized StatePositionsHandler reuse, the crux hunting /
identification / testing / resolution handlers, the identify-phase loop
routing (issue #22 mechanism), and method-level assembly.
"""

from consensus.methods.base import LINEAR_NEXT
from consensus.methods.phases._crux_helpers import (
    MAX_CRUX_SEARCH_ROUNDS,
    MAX_HUNT_ROUNDS,
    MAX_IDENTIFY_ATTEMPTS,
    VERDICT_FACTUAL,
    VERDICT_NONE,
    VERDICT_VALUES,
    record_cruxes,
)
from consensus.methods.phases.hunt_cruxes import HuntCruxesHandler
from consensus.methods.phases.identify_crux import IdentifyCruxHandler
from consensus.methods.phases.state_positions import StatePositionsHandler
from consensus.models import Discussion, Entity, EntityType

CLAIM_A = "Remote work reduces measured team productivity"
CLAIM_B = "Office presence improves informal knowledge transfer"


def _entity(eid: int = 1, name: str = "Alice") -> Entity:
    return Entity(id=eid, name=name, entity_type=EntityType.AI)


def _crux_discussion(phase: str = "hunt_cruxes", **state) -> Discussion:
    disc = Discussion(topic="Should our company go remote-first?",
                      discussion_method="double_crux",
                      moderator_id=99)
    disc.method_state = {
        "current_phase": phase, "phase_round": 1,
        "positions": {}, "cruxes": [],
        "crux_verdict": "", "shared_crux": {},
        "identify_attempts": 0, "crux_search_rounds": 1,
        "resolutions": [], "crux_map": {},
    }
    disc.method_state.update(state)
    return disc


def _payload_crux(claim: str = CLAIM_A, belief: float = 0.8) -> dict:
    return {"claim": claim, "belief": belief,
            "why_pivotal": "My position rests on this."}


def _adv_discussion() -> Discussion:
    return Discussion(topic="Test topic",
                      discussion_method="adversarial_collab",
                      moderator_id=99)


class TestStatePositionsContextLabel:
    def test_default_label_preserved(self):
        prompt = StatePositionsHandler().get_system_prompt(
            _entity(), _adv_discussion())
        assert "Adversarial" in prompt

    def test_custom_label(self):
        handler = StatePositionsHandler(context_label="a Double Crux session")
        prompt = handler.get_system_prompt(_entity(), _adv_discussion())
        assert "Double Crux session" in prompt
        assert "Adversarial" not in prompt


class TestHuntCruxesPrompts:
    def test_prompts_name_the_tool(self):
        handler = HuntCruxesHandler()
        disc = _crux_discussion()
        assert "submit_cruxes" in handler.get_system_prompt(_entity(), disc)
        assert "submit_cruxes" in handler.get_turn_prompt(_entity(), disc)

    def test_system_prompt_asks_the_canonical_question(self):
        prompt = HuntCruxesHandler().get_system_prompt(
            _entity(), _crux_discussion())
        assert "change your mind" in prompt

    def test_later_search_rounds_ask_for_convergence(self):
        handler = HuntCruxesHandler()
        first = handler.get_system_prompt(
            _entity(), _crux_discussion(crux_search_rounds=1))
        later = handler.get_system_prompt(
            _entity(), _crux_discussion(crux_search_rounds=2))
        assert first != later
        assert "shared" in later.lower()


class TestHuntCruxesProcessing:
    def test_structured_records_cruxes(self):
        handler = HuntCruxesHandler()
        disc = _crux_discussion()
        result = handler.process_structured_response(
            {"cruxes": [_payload_crux()], "reasoning": "Traced it."},
            _entity(), disc)
        assert len(disc.method_state["cruxes"]) == 1
        assert CLAIM_A in result.display_content
        assert "Traced it." in result.display_content

    def test_free_text_json_block_records_cruxes(self):
        handler = HuntCruxesHandler()
        disc = _crux_discussion()
        content = ('```json\n{"cruxes": [{"claim": "' + CLAIM_A
                   + '", "belief": 0.7, "why_pivotal": "core"}]}\n```')
        handler.process_response(content, _entity(), disc)
        assert disc.method_state["cruxes"][0]["claim"] == CLAIM_A

    def test_free_text_unparseable_records_nothing(self):
        handler = HuntCruxesHandler()
        disc = _crux_discussion()
        handler.process_response("I have no idea.", _entity(), disc)
        assert disc.method_state["cruxes"] == []

    def test_validate_output_delegates(self):
        handler = HuntCruxesHandler()
        disc = _crux_discussion()
        assert handler.validate_output(
            {"cruxes": [_payload_crux()], "reasoning": "r"},
            _entity(), disc) == ""
        assert handler.validate_output({"cruxes": []}, _entity(), disc) != ""


class TestHuntCruxesAdvancement:
    def test_stays_without_cruxes(self):
        handler = HuntCruxesHandler()
        disc = _crux_discussion(phase_round=2)
        assert handler.should_advance(disc) is False

    def test_advances_with_cruxes_after_round(self):
        handler = HuntCruxesHandler()
        disc = _crux_discussion(phase_round=2)
        record_cruxes(disc.method_state, _entity(), [_payload_crux()])
        assert handler.should_advance(disc) is True

    def test_gives_up_after_cap(self):
        handler = HuntCruxesHandler()
        disc = _crux_discussion(phase_round=MAX_HUNT_ROUNDS + 1)
        assert handler.should_advance(disc) is True

    def test_aborts_method_when_no_cruxes_after_cap(self):
        handler = HuntCruxesHandler()
        disc = _crux_discussion(phase_round=MAX_HUNT_ROUNDS + 1)
        assert handler.next_phase(disc) is None
        assert handler.get_method_complete_message(disc) != ""

    def test_no_abort_when_cruxes_exist(self):
        handler = HuntCruxesHandler()
        disc = _crux_discussion(phase_round=2)
        record_cruxes(disc.method_state, _entity(), [_payload_crux()])
        assert handler.next_phase(disc) == LINEAR_NEXT
        assert handler.get_method_complete_message(disc) == ""


def _hunted_discussion(**state) -> Discussion:
    disc = _crux_discussion(phase="identify_crux", **state)
    record_cruxes(disc.method_state, _entity(1, "Alice"),
                  [_payload_crux(CLAIM_A, 0.9)])
    record_cruxes(disc.method_state, _entity(2, "Bob"),
                  [_payload_crux(CLAIM_B, 0.4)])
    return disc


class TestIdentifyCruxBasics:
    def test_moderator_only_turn_order(self):
        disc = _hunted_discussion()
        assert IdentifyCruxHandler().get_turn_order([1, 2, 3], disc) == [99]

    def test_prompts_show_cruxes_and_name_the_tool(self):
        handler = IdentifyCruxHandler()
        disc = _hunted_discussion()
        disc.method_state["positions"] = {"Alice": "Remote-first"}
        system = handler.get_system_prompt(_entity(99, "Mod"), disc)
        assert CLAIM_A in system and CLAIM_B in system
        assert "Remote-first" in system
        assert "submit_crux_selection" in handler.get_turn_prompt(
            _entity(99, "Mod"), disc)

    def test_validate_output_uses_recorded_ids(self):
        handler = IdentifyCruxHandler()
        disc = _hunted_discussion()
        good = {"verdict": "factual", "crux_ids": [1, 2],
                "claim": CLAIM_A, "reasoning": "overlap"}
        assert handler.validate_output(good, _entity(99), disc) == ""
        bad = {"verdict": "factual", "crux_ids": [9],
               "claim": CLAIM_A, "reasoning": "overlap"}
        assert handler.validate_output(bad, _entity(99), disc) != ""


class TestIdentifyCruxProcessing:
    def test_structured_records_selection(self):
        handler = IdentifyCruxHandler()
        disc = _hunted_discussion()
        result = handler.process_structured_response(
            {"verdict": "factual", "crux_ids": [1], "claim": CLAIM_A,
             "reasoning": "Both circle it."},
            _entity(99, "Mod"), disc)
        assert disc.method_state["crux_verdict"] == VERDICT_FACTUAL
        assert disc.method_state["shared_crux"]["claim"] == CLAIM_A
        assert "Both circle it." in result.display_content

    def test_free_text_json_records_selection(self):
        handler = IdentifyCruxHandler()
        disc = _hunted_discussion()
        content = ('```json\n{"verdict": "values", "claim": '
                   '"Autonomy matters more than throughput", '
                   '"reasoning": "value split"}\n```')
        handler.process_response(content, _entity(99, "Mod"), disc)
        assert disc.method_state["crux_verdict"] == VERDICT_VALUES

    def test_free_text_invalid_counts_attempt(self):
        handler = IdentifyCruxHandler()
        disc = _hunted_discussion()
        handler.process_response("Hard to say.", _entity(99, "Mod"), disc)
        assert disc.method_state["identify_attempts"] == 1
        assert disc.method_state["crux_verdict"] == ""

    def test_free_text_json_failing_validation_counts_attempt(self):
        handler = IdentifyCruxHandler()
        disc = _hunted_discussion()
        content = '```json\n{"verdict": "factual", "reasoning": "r"}\n```'
        handler.process_response(content, _entity(99, "Mod"), disc)
        assert disc.method_state["identify_attempts"] == 1


class TestIdentifyCruxRouting:
    def test_advances_on_verdict_or_give_up(self):
        handler = IdentifyCruxHandler()
        assert handler.should_advance(_hunted_discussion()) is False
        assert handler.should_advance(
            _hunted_discussion(crux_verdict=VERDICT_FACTUAL)) is True
        assert handler.should_advance(_hunted_discussion(
            identify_attempts=MAX_IDENTIFY_ATTEMPTS)) is True

    def test_factual_continues_linearly(self):
        disc = _hunted_discussion(crux_verdict=VERDICT_FACTUAL)
        assert IdentifyCruxHandler().next_phase(disc) == LINEAR_NEXT

    def test_values_jumps_to_resolve(self):
        disc = _hunted_discussion(crux_verdict=VERDICT_VALUES)
        assert IdentifyCruxHandler().next_phase(disc) == "resolve"

    def test_none_with_rounds_left_loops_back(self):
        disc = _hunted_discussion(crux_verdict=VERDICT_NONE,
                                  crux_search_rounds=1)
        assert IdentifyCruxHandler().next_phase(disc) == "hunt_cruxes"
        state = disc.method_state
        assert state["crux_search_rounds"] == 2
        assert state["crux_verdict"] == ""  # reset for the next visit

    def test_none_exhausted_goes_to_resolve(self):
        disc = _hunted_discussion(crux_verdict=VERDICT_NONE,
                                  crux_search_rounds=MAX_CRUX_SEARCH_ROUNDS)
        assert IdentifyCruxHandler().next_phase(disc) == "resolve"
        assert disc.method_state["crux_verdict"] == VERDICT_NONE

    def test_give_up_without_verdict_treated_as_none(self):
        disc = _hunted_discussion(
            identify_attempts=MAX_IDENTIFY_ATTEMPTS,
            crux_search_rounds=MAX_CRUX_SEARCH_ROUNDS)
        assert IdentifyCruxHandler().next_phase(disc) == "resolve"
        assert disc.method_state["crux_verdict"] == VERDICT_NONE

    def test_give_up_without_verdict_loops_when_rounds_left(self):
        disc = _hunted_discussion(identify_attempts=MAX_IDENTIFY_ATTEMPTS,
                                  crux_search_rounds=1)
        handler = IdentifyCruxHandler()
        assert handler.next_phase(disc) == "hunt_cruxes"
        assert disc.method_state["identify_attempts"] == 0  # fresh visit
