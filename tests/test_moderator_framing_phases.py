"""Regression tests for moderator framing/capture phases (GitHub issue #15).

Phases documented as "moderator frames / moderator captures" must actually
route the moderator through the turn order and capture their output on a
code path that runs in a normal discussion (process_response of a
moderator-only phase), and condition-based phases must not loop forever
when parsing never succeeds.
"""

import pytest

from consensus.methods.phases.counterfactual_extract import (
    ExtractClaimsHandler,
)
from consensus.methods.phases.define_criteria import DefineCriteriaHandler
from consensus.methods.phases.distill_skeleton import DistillSkeletonHandler
from consensus.methods.phases.frame_hypotheses import FrameHypothesesHandler
from consensus.methods.phases.frame_premortem import FramePremortemHandler
from consensus.methods.phases.hypothesize import HypothesizeHandler
from consensus.methods.phases.prior_beliefs import PriorBeliefsHandler
from consensus.models import Discussion, Entity, EntityType


def _entity(eid: int, name: str) -> Entity:
    return Entity(id=eid, name=name, entity_type=EntityType.HUMAN)


@pytest.fixture
def discussion():
    mod = _entity(1, "Mod")
    p1 = _entity(2, "P1")
    p2 = _entity(3, "P2")
    return Discussion(
        topic="Test topic",
        entities=[mod, p1, p2],
        moderator_id=mod.id,
        turn_order=[p1.id, p2.id],
        base_turn_order=[p1.id, p2.id],
        method_state={},
    )


class TestFrameHypotheses:
    """Belief Diffusion framing is a moderator-only phase."""

    def test_turn_order_is_moderator_only(self, discussion):
        h = FrameHypothesesHandler()
        order = h.get_turn_order([2, 3], discussion)
        assert order == [discussion.moderator_id]

    def test_moderator_prompts_are_not_empty(self, discussion):
        h = FrameHypothesesHandler()
        mod = discussion.entities[0]
        assert h.get_system_prompt(mod, discussion)
        assert "hypotheses" in h.get_turn_prompt(mod, discussion).lower()

    def test_gives_up_after_max_attempts(self, discussion):
        """Unparseable framing responses must not loop forever."""
        h = FrameHypothesesHandler()
        discussion.method_state = h.init_state(discussion)
        mod = discussion.entities[0]
        for _ in range(3):
            assert h.should_advance(discussion) is False
            h.process_response("no list here at all", mod, discussion)
        assert h.should_advance(discussion) is True

    def test_advances_once_hypotheses_extracted(self, discussion):
        h = FrameHypothesesHandler()
        discussion.method_state = h.init_state(discussion)
        mod = discussion.entities[0]
        h.process_response(
            "1. The effect is caused by measurement error\n"
            "2. The effect is a genuine physical phenomenon\n"
            "3. The effect is caused by environmental confounders\n",
            mod, discussion,
        )
        assert discussion.method_state["hypotheses"]
        assert h.should_advance(discussion) is True

    def test_prior_transition_warns_when_framing_gave_up(self, discussion):
        """The next phase's announcement must surface the failure to users
        instead of rendering an empty hypothesis list.

        (After ``MAX_FRAMING_ATTEMPTS`` unparseable responses the frame
        phase advances with ``hypotheses`` still empty.)
        """
        h = PriorBeliefsHandler()
        discussion.method_state = {"hypotheses": []}
        msg = h.get_transition_message(discussion)
        assert "could not" in msg.lower()
        assert "hypothesis" in msg.lower()

    def test_prior_transition_normal_when_hypotheses_present(self, discussion):
        h = PriorBeliefsHandler()
        discussion.method_state = {"hypotheses": ["A", "B"]}
        msg = h.get_transition_message(discussion)
        assert "**H1:** A" in msg
        assert "could not" not in msg.lower()


class TestFramePremortem:
    """Premortem framing is a moderator-only phase with real prompts."""

    def test_turn_order_is_moderator_only(self, discussion):
        h = FramePremortemHandler()
        assert h.get_turn_order([2, 3], discussion) == [discussion.moderator_id]

    def test_moderator_prompts_are_not_empty(self, discussion):
        h = FramePremortemHandler()
        mod = discussion.entities[0]
        assert h.get_system_prompt(mod, discussion)
        assert h.get_turn_prompt(mod, discussion)


class TestCounterfactualConclusionCapture:
    """The extract phase captures the preliminary conclusion it tests."""

    def test_conclusion_parsed_from_extract_response(self, discussion):
        h = ExtractClaimsHandler()
        discussion.method_state = h.init_state(discussion)
        mod = discussion.entities[0]
        content = (
            "CONCLUSION: Remote work increases overall productivity for "
            "knowledge workers.\n\n"
            "1. Commuting time is reclaimed as productive working time\n"
            "2. Fewer office interruptions improve deep-work quality\n"
            "3. Asynchronous collaboration does not degrade output\n"
        )
        h.process_response(content, mod, discussion)

        state = discussion.method_state
        assert state["claims"], "claims must still be extracted"
        assert "Remote work increases overall productivity" in (
            state.get("preliminary_conclusion") or ""
        )
        # The claim list must not contain the conclusion line itself
        assert all(
            "CONCLUSION" not in c["text"] for c in state["claims"]
        )

    def test_prompt_asks_for_conclusion_when_missing(self, discussion):
        h = ExtractClaimsHandler()
        discussion.method_state = h.init_state(discussion)
        mod = discussion.entities[0]
        prompt = h.get_turn_prompt(mod, discussion)
        assert "CONCLUSION:" in prompt


class TestDistillRichSummaryCapture:
    """The distill phase captures the rich-reasoning summary."""

    def test_summary_parsed_from_distill_response(self, discussion):
        h = DistillSkeletonHandler()
        discussion.method_state = h.init_state(discussion)
        mod = discussion.entities[0]
        content = (
            "RICH SUMMARY: The discussion leaned heavily on the Titanic "
            "analogy and an appeal to expert authority.\n\n"
            "```json\n"
            '{"premises": [{"id": "P1", "text": "p"}],\n'
            ' "inferences": [{"id": "I1", "from": ["P1"], "text": "i"}],\n'
            ' "conclusions": [{"id": "C1", "from": ["I1"], "text": "c"}]}\n'
            "```"
        )
        h.process_response(content, mod, discussion)

        state = discussion.method_state
        assert state["skeleton"], "skeleton must still be extracted"
        assert "Titanic analogy" in (state.get("rich_reasoning_summary") or "")


class TestUncappedPhaseLoops:
    """Condition-based phases must give up after bounded rounds."""

    def test_hypothesize_gives_up_eventually(self, discussion):
        h = HypothesizeHandler()
        discussion.method_state = {"hypotheses": [], "phase_round": 99}
        assert h.should_advance(discussion) is True

    def test_define_criteria_gives_up_eventually(self, discussion):
        h = DefineCriteriaHandler()
        discussion.method_state = {"criteria": [], "phase_round": 99}
        assert h.should_advance(discussion) is True
