"""Structured-output coverage for the Double Crux phases (#23 convention).

The forced submit_cruxes / submit_crux_selection / submit_resolution
tools replace free-text parsing for tool-capable models; the free-text
paths remain for humans.  Mirrors test_ngt_structured.
"""

from consensus.methods import get_method
from consensus.methods.phases._crux_helpers import (
    CRUX_SELECTION_TOOL_PARAMETERS,
    CRUXES_TOOL_PARAMETERS,
    POLL_BELIEF_TOOL_PARAMETERS,
    RESOLUTION_TOOL_PARAMETERS,
    VERDICT_FACTUAL,
    record_cruxes,
)
from consensus.methods.phases.hunt_cruxes import HuntCruxesHandler
from consensus.methods.phases.identify_crux import IdentifyCruxHandler
from consensus.methods.phases.poll_belief import PollBeliefHandler
from consensus.methods.phases.resolve_crux import ResolveCruxHandler
from consensus.methods.phases.state_positions import StatePositionsHandler
from consensus.methods.phases.test_crux import TestCruxHandler
from consensus.models import Discussion, Entity, EntityType

CLAIM = "Remote work reduces measured team productivity"


def _entity(eid: int = 1, name: str = "Alice") -> Entity:
    return Entity(id=eid, name=name, entity_type=EntityType.AI)


def _discussion(phase: str, **state) -> Discussion:
    disc = Discussion(topic="Should our company go remote-first?",
                      discussion_method="double_crux",
                      moderator_id=99)
    disc.method_state = get_method("double_crux").init_state(disc)
    disc.method_state["current_phase"] = phase
    disc.method_state.update(state)
    return disc


class TestStructuredFlags:
    def test_hunt_identify_resolve_require_structured(self):
        assert HuntCruxesHandler().requires_structured_output is True
        assert IdentifyCruxHandler().requires_structured_output is True
        assert ResolveCruxHandler().requires_structured_output is True

    def test_positions_and_test_crux_do_not(self):
        assert StatePositionsHandler().requires_structured_output is False
        assert TestCruxHandler().requires_structured_output is False

    def test_method_requires_structured_output(self):
        assert (get_method("double_crux").requires_structured_output()
                is True)

    def test_poll_requires_structured(self):
        assert PollBeliefHandler().requires_structured_output is True


class TestOutputToolSpecs:
    def test_hunt_spec(self):
        spec = HuntCruxesHandler().get_output_tool(
            _entity(), _discussion("hunt_cruxes"))
        assert spec.name == "submit_cruxes"
        assert spec.parameters is CRUXES_TOOL_PARAMETERS

    def test_identify_spec(self):
        spec = IdentifyCruxHandler().get_output_tool(
            _entity(99, "Mod"), _discussion("identify_crux"))
        assert spec.name == "submit_crux_selection"
        assert spec.parameters is CRUX_SELECTION_TOOL_PARAMETERS

    def test_resolve_spec(self):
        spec = ResolveCruxHandler().get_output_tool(
            _entity(), _discussion("resolve"))
        assert spec.name == "submit_resolution"
        assert spec.parameters is RESOLUTION_TOOL_PARAMETERS

    def test_poll_spec(self):
        spec = PollBeliefHandler().get_output_tool(
            _entity(), _discussion("poll_belief",
                                   shared_crux={"claim": CLAIM,
                                                "initial_beliefs": {}}))
        assert spec.name == "submit_crux_belief"
        assert spec.parameters is POLL_BELIEF_TOOL_PARAMETERS


class TestPromptsNameTheTool:
    def test_hunt_prompts(self):
        handler = HuntCruxesHandler()
        disc = _discussion("hunt_cruxes")
        assert "submit_cruxes" in handler.get_system_prompt(_entity(), disc)
        assert "submit_cruxes" in handler.get_turn_prompt(_entity(), disc)

    def test_identify_turn_prompt(self):
        handler = IdentifyCruxHandler()
        disc = _discussion("identify_crux")
        assert "submit_crux_selection" in handler.get_turn_prompt(
            _entity(99, "Mod"), disc)

    def test_resolve_prompts(self):
        handler = ResolveCruxHandler()
        disc = _discussion("resolve")
        assert "submit_resolution" in handler.get_system_prompt(
            _entity(), disc)
        assert "submit_resolution" in handler.get_turn_prompt(
            _entity(), disc)

    def test_poll_prompts(self):
        handler = PollBeliefHandler()
        disc = _discussion("poll_belief",
                           shared_crux={"claim": CLAIM, "initial_beliefs": {}})
        assert "submit_crux_belief" in handler.get_system_prompt(
            _entity(), disc)
        assert "submit_crux_belief" in handler.get_turn_prompt(
            _entity(), disc)


class TestStructuredMatchesFreeTextPaths:
    def test_hunt_structured_and_free_text_produce_same_state(self):
        handler = HuntCruxesHandler()
        payload_crux = {"claim": CLAIM, "belief": 0.7,
                        "why_pivotal": "core to my position"}

        disc_a = _discussion("hunt_cruxes")
        handler.process_structured_response(
            {"cruxes": [payload_crux], "reasoning": "Traced it."},
            _entity(), disc_a)

        disc_b = _discussion("hunt_cruxes")
        content = ('```json\n{"cruxes": [{"claim": "' + CLAIM
                   + '", "belief": 0.7, "why_pivotal": '
                   '"core to my position"}]}\n```')
        handler.process_response(content, _entity(), disc_b)

        assert disc_a.method_state["cruxes"] == disc_b.method_state["cruxes"]

    def test_identify_structured_and_free_text_produce_same_state(self):
        handler = IdentifyCruxHandler()
        payload = {"verdict": "factual", "crux_ids": [1], "claim": CLAIM,
                   "reasoning": "Both pivot on it."}

        disc_a = _discussion("identify_crux")
        record_cruxes(disc_a.method_state, _entity(),
                      [{"claim": CLAIM, "belief": 0.7, "why_pivotal": "w"}])
        handler.process_structured_response(payload, _entity(99), disc_a)

        disc_b = _discussion("identify_crux")
        record_cruxes(disc_b.method_state, _entity(),
                      [{"claim": CLAIM, "belief": 0.7, "why_pivotal": "w"}])
        content = ('```json\n{"verdict": "factual", "crux_ids": [1], '
                   '"claim": "' + CLAIM + '", "reasoning": '
                   '"Both pivot on it."}\n```')
        handler.process_response(content, _entity(99), disc_b)

        assert disc_a.method_state["crux_verdict"] == VERDICT_FACTUAL
        assert (disc_a.method_state["shared_crux"]
                == disc_b.method_state["shared_crux"])

    def test_resolve_structured_and_free_text_produce_same_state(self):
        handler = ResolveCruxHandler()
        payload = {"stance": "updated",
                   "position": "Hybrid with quarterly on-sites",
                   "crux_belief": 0.5, "reasoning": "Data moved me."}

        disc_a = _discussion("resolve", crux_verdict=VERDICT_FACTUAL)
        handler.process_structured_response(payload, _entity(), disc_a)

        disc_b = _discussion("resolve", crux_verdict=VERDICT_FACTUAL)
        content = ('```json\n{"stance": "updated", "position": '
                   '"Hybrid with quarterly on-sites", "crux_belief": 0.5, '
                   '"reasoning": "Data moved me."}\n```')
        handler.process_response(content, _entity(), disc_b)

        assert (disc_a.method_state["resolutions"]
                == disc_b.method_state["resolutions"])

    def test_poll_structured_and_free_text_produce_same_state(self):
        handler = PollBeliefHandler()
        disc_a = _discussion("poll_belief",
                             shared_crux={"claim": CLAIM,
                                          "initial_beliefs": {}})
        handler.process_structured_response(
            {"belief": 0.4, "reasoning": "sceptical"}, _entity(), disc_a)

        disc_b = _discussion("poll_belief",
                             shared_crux={"claim": CLAIM,
                                          "initial_beliefs": {}})
        content = '```json\n{"belief": 0.4, "reasoning": "sceptical"}\n```'
        handler.process_response(content, _entity(), disc_b)

        assert (disc_a.method_state["poll_beliefs"]
                == disc_b.method_state["poll_beliefs"])
