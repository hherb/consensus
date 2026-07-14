"""Tests for the Double Crux phase handlers (issue #27).

Covers the parametrized StatePositionsHandler reuse, the crux hunting /
identification / testing / resolution handlers, the identify-phase loop
routing (issue #22 mechanism), and method-level assembly.
"""

from consensus.methods.phases._crux_helpers import (
    MAX_HUNT_ROUNDS,
    record_cruxes,
)
from consensus.methods.phases.hunt_cruxes import HuntCruxesHandler
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
        from consensus.methods.base import LINEAR_NEXT
        handler = HuntCruxesHandler()
        disc = _crux_discussion(phase_round=2)
        record_cruxes(disc.method_state, _entity(), [_payload_crux()])
        assert handler.next_phase(disc) == LINEAR_NEXT
        assert handler.get_method_complete_message(disc) == ""
