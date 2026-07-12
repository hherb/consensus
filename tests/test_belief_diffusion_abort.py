"""Tests for Belief Diffusion method-abort (issue #30).

When the framing phase exhausts ``MAX_FRAMING_ATTEMPTS`` without a
parseable hypothesis list, the method must end (``method_complete``)
with a clear user-facing message instead of marching through the
prior/diffuse/diagnose phases against an empty hypothesis list.
"""

import pytest

from consensus.app_discussion_flow import complete_turn, submit_human_message
from consensus.methods.base import LINEAR_NEXT, DiscussionMethod, Phase
from consensus.methods.belief_diffusion import BeliefDiffusion
from consensus.methods.phase_handler import PhaseHandler
from consensus.methods.phases.frame_hypotheses import (
    MAX_FRAMING_ATTEMPTS,
    FrameHypothesesHandler,
)
from consensus.moderator import Moderator
from consensus.models import Discussion, Entity, MessageRole
from consensus.pricing import PricingCache


@pytest.fixture
def method():
    return BeliefDiffusion()


@pytest.fixture
def discussion(method):
    disc = Discussion(topic="Test question", discussion_method=method.name)
    disc.method_state = method.init_state(disc)
    return disc


def _give_up_state(discussion: Discussion) -> None:
    """Put the discussion in the framing-gave-up state."""
    discussion.method_state["hypotheses"] = []
    discussion.method_state["framing_attempts"] = MAX_FRAMING_ATTEMPTS


# ---------------------------------------------------------------------------
# Handler-level next_phase behavior
# ---------------------------------------------------------------------------


class TestFrameNextPhase:
    def test_linear_next_when_hypotheses_parsed(self, discussion):
        handler = FrameHypothesesHandler()
        discussion.method_state["hypotheses"] = ["H1 text", "H2 text"]
        assert handler.next_phase(discussion) == LINEAR_NEXT

    def test_aborts_when_attempts_exhausted_without_hypotheses(
        self, discussion,
    ):
        handler = FrameHypothesesHandler()
        _give_up_state(discussion)
        assert handler.next_phase(discussion) is None

    def test_linear_next_while_attempts_remain(self, discussion):
        handler = FrameHypothesesHandler()
        discussion.method_state["framing_attempts"] = (
            MAX_FRAMING_ATTEMPTS - 1
        )
        assert handler.next_phase(discussion) == LINEAR_NEXT


class TestMethodAbort:
    def test_advance_phase_ends_method_instead_of_entering_prior(
        self, method, discussion,
    ):
        _give_up_state(discussion)
        assert method.advance_phase(discussion) is None
        assert discussion.method_state["current_phase"] == "frame"

    def test_advance_phase_enters_prior_when_hypotheses_exist(
        self, method, discussion,
    ):
        discussion.method_state["hypotheses"] = ["H1 text", "H2 text"]
        new_phase = method.advance_phase(discussion)
        assert new_phase is not None and new_phase.name == "prior"


# ---------------------------------------------------------------------------
# User-facing method-complete message
# ---------------------------------------------------------------------------


class _StubHandler(PhaseHandler):
    phase = Phase("a", "Phase A")

    def get_system_prompt(self, entity: Entity,
                          discussion: Discussion) -> str:
        return ""

    def get_turn_prompt(self, entity: Entity,
                        discussion: Discussion) -> str:
        return ""


class _StubMethod(DiscussionMethod):
    name = "_test_stub"
    display_name = "Stub"
    description = "test"
    phase_handlers = (_StubHandler(),)


class TestMethodCompleteMessage:
    def test_base_handler_default_is_empty(self):
        disc = Discussion(topic="t")
        assert _StubHandler().get_method_complete_message(disc) == ""

    def test_base_method_default_is_empty(self):
        m = _StubMethod()
        disc = Discussion(topic="t", discussion_method=m.name)
        disc.method_state = m.init_state(disc)
        assert m.get_method_complete_message(disc) == ""

    def test_frame_abort_message_explains_failure(self, method, discussion):
        _give_up_state(discussion)
        msg = method.get_method_complete_message(discussion)
        assert "framing" in msg.lower()
        assert "hypothes" in msg.lower()

    def test_no_abort_message_when_hypotheses_exist(self, method, discussion):
        discussion.method_state["hypotheses"] = ["H1 text"]
        assert method.get_method_complete_message(discussion) == ""


# ---------------------------------------------------------------------------
# Conclusion prompt must not pretend a diagnosis is possible
# ---------------------------------------------------------------------------


class TestConclusionPrompt:
    def test_conclusion_acknowledges_framing_failure(
        self, method, discussion,
    ):
        _give_up_state(discussion)
        prompt = method.get_conclusion_prompt(discussion)
        assert "hypothes" in prompt.lower()
        # Must not ask for the standard belief-trajectory diagnosis.
        assert "Belief trajectories" not in prompt

    def test_conclusion_unchanged_with_hypotheses(self, method, discussion):
        discussion.method_state["hypotheses"] = ["H1 text", "H2 text"]
        prompt = method.get_conclusion_prompt(discussion)
        assert "Belief trajectories" in prompt


# ---------------------------------------------------------------------------
# Integration: framing gives up through the real pipeline
# ---------------------------------------------------------------------------


def _entity(db, name: str) -> Entity:
    eid = db.add_entity(name, "human", "#123456")
    return Entity.from_db_row(db.get_entity(eid))


class TestAbortThroughPipeline:
    """Drive complete_turn with a human moderator whose framing responses
    never contain a parseable numbered list."""

    @pytest.mark.asyncio
    async def test_exhausted_framing_completes_method_with_warning(
        self, tmp_db,
    ):
        method = BeliefDiffusion()
        mod = _entity(tmp_db, "Mod")
        parts = [_entity(tmp_db, "P1"), _entity(tmp_db, "P2")]
        disc = Discussion(
            topic="Unframeable question",
            entities=[mod] + parts,
            moderator_id=mod.id,
            # Frame phase is moderator-only.
            turn_order=[mod.id],
            base_turn_order=[p.id for p in parts],
            current_turn_index=0,
            turn_number=1,
            is_active=True,
            status="active",
            discussion_method=method.name,
        )
        disc.id = tmp_db.create_discussion(disc.topic, mod.id)
        disc.method_state = method.init_state(disc)

        moderator = Moderator(disc, tmp_db)
        pricing = PricingCache(tmp_db.conn, tmp_db._lock)

        method_complete = False
        for _ in range(MAX_FRAMING_ATTEMPTS):
            submitted = submit_human_message(
                disc, tmp_db, mod.id,
                "I am sorry, I cannot think of any hypotheses here.",
            )
            assert "error" not in submitted
            result = await complete_turn(
                disc, moderator, tmp_db, pricing,
                get_state_fn=lambda: {},
                moderator_summary="Noted.",
            )
            assert "error" not in result
            if result.get("method_complete"):
                method_complete = True
                break

        assert method_complete, "method did not end after framing gave up"
        # Prior phase must never have started.
        assert disc.method_state["current_phase"] == "frame"
        assert disc.method_state["hypotheses"] == []

        # A clear user-facing system message must have been posted.
        system_msgs = [m for m in disc.messages
                       if m.role == MessageRole.SYSTEM]
        assert any("framing" in m.content.lower()
                   and "hypothes" in m.content.lower()
                   for m in system_msgs)
