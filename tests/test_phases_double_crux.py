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
from consensus.methods.phases.resolve_crux import ResolveCruxHandler
from consensus.methods.phases.test_crux import TestCruxHandler
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
        # Belief carry-over depends on the shared claim keeping the
        # cited cruxes' polarity — the prompt must say so.
        assert "polarity" in system.lower()
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


def _factual_discussion(phase: str = "test_crux", **state) -> Discussion:
    disc = _hunted_discussion(**state)
    disc.method_state["current_phase"] = phase
    disc.method_state["crux_verdict"] = VERDICT_FACTUAL
    disc.method_state["shared_crux"] = {
        "claim": CLAIM_A, "description": "", "source_crux_ids": [1],
        "initial_beliefs": {"Alice": 0.9},
    }
    return disc


class TestTestCruxHandler:
    def test_free_text_phase(self):
        handler = TestCruxHandler()
        assert handler.requires_structured_output is False
        assert handler.get_output_tool(
            _entity(), _factual_discussion()) is None
        assert handler.phase.rounds > 0

    def test_prompt_focuses_on_the_crux(self):
        disc = _factual_discussion()
        system = TestCruxHandler().get_system_prompt(_entity(), disc)
        assert CLAIM_A in system
        assert "crux" in system.lower()

    def test_turn_prompt_mentions_evidence(self):
        disc = _factual_discussion()
        assert "evidence" in TestCruxHandler().get_turn_prompt(
            _entity(), disc).lower()

    def test_test_crux_phase_tracks_evidence(self):
        assert TestCruxHandler().phase.track_evidence is True


class TestResolveCruxPrompts:
    def test_factual_prompt_requires_belief(self):
        disc = _factual_discussion(phase="resolve")
        handler = ResolveCruxHandler()
        system = handler.get_system_prompt(_entity(), disc)
        assert "submit_resolution" in system
        assert CLAIM_A in system
        assert "probability" in system.lower()

    def test_values_prompt_mentions_the_difference(self):
        disc = _crux_discussion(
            phase="resolve", crux_verdict=VERDICT_VALUES,
            shared_crux={"claim": "", "description": "Autonomy over output",
                         "source_crux_ids": [], "initial_beliefs": {}})
        system = ResolveCruxHandler().get_system_prompt(_entity(), disc)
        assert "Autonomy over output" in system

    def test_none_prompt_asks_for_the_map(self):
        disc = _crux_discussion(phase="resolve", crux_verdict=VERDICT_NONE)
        system = ResolveCruxHandler().get_system_prompt(_entity(), disc)
        assert "reduces" in system.lower() or "map" in system.lower()


class TestResolveCruxProcessing:
    def _payload(self) -> dict:
        return {"stance": "updated",
                "position": "Hybrid with quarterly on-sites",
                "crux_belief": 0.55, "reasoning": "The trial data moved me."}

    def test_validate_requires_belief_iff_factual(self):
        handler = ResolveCruxHandler()
        payload = self._payload()
        del payload["crux_belief"]
        assert handler.validate_output(
            payload, _entity(), _factual_discussion(phase="resolve")) != ""
        disc_values = _crux_discussion(phase="resolve",
                                       crux_verdict=VERDICT_VALUES)
        assert handler.validate_output(payload, _entity(), disc_values) == ""

    def test_structured_records_resolution(self):
        handler = ResolveCruxHandler()
        disc = _factual_discussion(phase="resolve")
        result = handler.process_structured_response(
            self._payload(), _entity(), disc)
        assert disc.method_state["resolutions"][0]["crux_belief"] == 0.55
        assert "Hybrid with quarterly on-sites" in result.display_content

    def test_free_text_json_records_resolution(self):
        handler = ResolveCruxHandler()
        disc = _factual_discussion(phase="resolve")
        content = ('```json\n{"stance": "unchanged", "position": '
                   '"Remote-first remains right", "reasoning": "unmoved"}'
                   "\n```")
        handler.process_response(content, _entity(), disc)
        assert disc.method_state["resolutions"][0]["stance"] == "unchanged"

    def test_free_text_plain_records_nothing(self):
        handler = ResolveCruxHandler()
        disc = _factual_discussion(phase="resolve")
        handler.process_response("I feel the same.", _entity(), disc)
        assert disc.method_state["resolutions"] == []


class TestResolveCruxAdvancement:
    def test_stays_without_resolutions(self):
        disc = _factual_discussion(phase="resolve", phase_round=2)
        assert ResolveCruxHandler().should_advance(disc) is False

    def test_advances_with_resolutions(self):
        handler = ResolveCruxHandler()
        disc = _factual_discussion(phase="resolve", phase_round=2)
        handler.process_structured_response(
            {"stance": "unchanged", "position": "Remote-first remains",
             "crux_belief": 0.8, "reasoning": "r"}, _entity(), disc)
        assert handler.should_advance(disc) is True

    def test_gives_up_after_cap(self):
        from consensus.methods.phases._crux_helpers import MAX_RESOLVE_ROUNDS
        disc = _factual_discussion(phase="resolve",
                                   phase_round=MAX_RESOLVE_ROUNDS + 1)
        assert ResolveCruxHandler().should_advance(disc) is True

    def test_waits_for_stragglers_when_roster_known(self):
        # One of two participants resolved: keep the phase open so the
        # straggler's belief restatement gets further rounds.
        handler = ResolveCruxHandler()
        disc = _factual_discussion(phase="resolve", phase_round=2)
        disc.turn_order = [1, 2]
        handler.process_structured_response(
            {"stance": "unchanged", "position": "Remote-first remains",
             "crux_belief": 0.8, "reasoning": "r"}, _entity(1), disc)
        assert handler.should_advance(disc) is False

    def test_advances_once_all_participants_resolved(self):
        handler = ResolveCruxHandler()
        disc = _factual_discussion(phase="resolve", phase_round=1)
        disc.turn_order = [1, 2]
        for eid, name in ((1, "Alice"), (2, "Bob")):
            handler.process_structured_response(
                {"stance": "unchanged", "position": "Remote-first remains",
                 "crux_belief": 0.8, "reasoning": "r"},
                _entity(eid, name), disc)
        assert handler.should_advance(disc) is True

    def test_gives_up_after_cap_with_roster_known(self):
        from consensus.methods.phases._crux_helpers import MAX_RESOLVE_ROUNDS
        handler = ResolveCruxHandler()
        disc = _factual_discussion(phase="resolve",
                                   phase_round=MAX_RESOLVE_ROUNDS + 1)
        disc.turn_order = [1, 2]
        handler.process_structured_response(
            {"stance": "unchanged", "position": "Remote-first remains",
             "crux_belief": 0.8, "reasoning": "r"}, _entity(1), disc)
        assert handler.should_advance(disc) is True

    def test_next_phase_builds_crux_map(self):
        handler = ResolveCruxHandler()
        disc = _factual_discussion(phase="resolve", phase_round=2)
        handler.process_structured_response(
            {"stance": "updated", "position": "Hybrid works best",
             "crux_belief": 0.55, "reasoning": "r"}, _entity(), disc)
        assert handler.next_phase(disc) == LINEAR_NEXT
        crux_map = disc.method_state["crux_map"]
        assert crux_map["verdict"] == VERDICT_FACTUAL
        assert crux_map["belief_shifts"]["Alice"]["shift"] == -0.35


class TestDoubleCruxMethod:
    def _method(self):
        from consensus.methods import get_method
        return get_method("double_crux")

    def _discussion(self) -> Discussion:
        disc = Discussion(topic="Should our company go remote-first?",
                          discussion_method="double_crux",
                          moderator_id=99)
        disc.method_state = self._method().init_state(disc)
        return disc

    def test_registered_with_expected_phases(self):
        method = self._method()
        assert method.name == "double_crux"
        assert [p.name for p in method.default_phases] == [
            "positions", "hunt_cruxes", "identify_crux", "test_crux",
            "resolve"]

    def test_requires_structured_output(self):
        assert self._method().requires_structured_output() is True

    def test_init_state_has_all_keys(self):
        state = self._discussion().method_state
        for key in ("positions", "cruxes", "crux_verdict", "shared_crux",
                    "identify_attempts", "crux_search_rounds",
                    "resolutions", "crux_map"):
            assert key in state, key

    def test_positions_prompt_uses_double_crux_label(self):
        disc = self._discussion()
        prompt = self._method().get_system_prompt(_entity(), disc)
        assert "Double Crux" in prompt
        assert "Adversarial" not in prompt

    def test_advance_phase_loops_on_none_verdict(self):
        method = self._method()
        disc = self._discussion()
        disc.method_state["current_phase"] = "identify_crux"
        disc.method_state["crux_verdict"] = VERDICT_NONE
        phase = method.advance_phase(disc)
        assert phase.name == "hunt_cruxes"
        assert disc.method_state["crux_search_rounds"] == 2

    def test_advance_phase_values_skips_testing(self):
        method = self._method()
        disc = self._discussion()
        disc.method_state["current_phase"] = "identify_crux"
        disc.method_state["crux_verdict"] = VERDICT_VALUES
        assert method.advance_phase(disc).name == "resolve"

    def test_worst_case_looping_never_trips_loop_guard(self):
        from consensus.methods.base import MAX_PHASE_VISITS_PER_PHASE
        # positions→hunt→(identify→hunt)×(MAX-1)→identify→resolve
        transitions = 2 + 2 * (MAX_CRUX_SEARCH_ROUNDS - 1) + 1
        method = self._method()
        cap = len(method.default_phases) * MAX_PHASE_VISITS_PER_PHASE
        assert transitions < cap

    def test_conclusion_prompt_factual(self):
        disc = self._discussion()
        disc.method_state["crux_verdict"] = VERDICT_FACTUAL
        disc.method_state["shared_crux"] = {
            "claim": CLAIM_A, "description": "", "source_crux_ids": [1],
            "initial_beliefs": {"Alice": 0.9}}
        prompt = self._method().get_conclusion_prompt(disc)
        assert CLAIM_A in prompt
        assert "belief" in prompt.lower()

    def test_conclusion_prompt_values(self):
        disc = self._discussion()
        disc.method_state["crux_verdict"] = VERDICT_VALUES
        disc.method_state["shared_crux"] = {
            "claim": "", "description": "Autonomy over output",
            "source_crux_ids": [], "initial_beliefs": {}}
        prompt = self._method().get_conclusion_prompt(disc)
        assert "values difference" in prompt.lower()
        assert "Autonomy over output" in prompt

    def test_conclusion_prompt_none(self):
        disc = self._discussion()
        disc.method_state["crux_verdict"] = VERDICT_NONE
        prompt = self._method().get_conclusion_prompt(disc)
        assert "no shared crux" in prompt.lower()


def test_conclusion_prompt_lists_evidence_basis():
    from consensus.methods.double_crux import DoubleCrux
    from consensus.models import Discussion
    d = Discussion(topic="t")
    d.method_state = {
        "crux_verdict": "factual",
        "shared_crux": {}, "positions": {}, "resolutions": [],
        "evidence_log": [
            {"entity_name": "Alice", "grounded": True,
             "sources": [{"type": "web", "url": "https://a.example"}]},
            {"entity_name": "Bob", "grounded": False, "sources": []},
        ],
    }
    prompt = DoubleCrux().get_conclusion_prompt(d)
    assert "Grounded contributions (1)" in prompt
    assert "https://a.example" in prompt
    assert "Reasoning-based" in prompt
    assert "Bob" in prompt
