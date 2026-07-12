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

    def test_gave_up_conclusion_names_the_framing_failure(
        self, method, discussion,
    ):
        _give_up_state(discussion)
        prompt = method.get_conclusion_prompt(discussion)
        assert "could not decompose" in prompt

    def test_early_manual_conclusion_does_not_blame_framing(
        self, method, discussion,
    ):
        # User concluded mid-framing with attempts remaining: framing
        # never failed, so the prompt must not claim that it did.
        discussion.method_state["hypotheses"] = []
        discussion.method_state["framing_attempts"] = 0
        prompt = method.get_conclusion_prompt(discussion)
        assert "could not decompose" not in prompt
        assert "concluded before" in prompt
        assert "Belief trajectories" not in prompt


# ---------------------------------------------------------------------------
# Integration: framing gives up through the real pipeline
# ---------------------------------------------------------------------------


def _entity(db, name: str) -> Entity:
    eid = db.add_entity(name, "human", "#123456")
    return Entity.from_db_row(db.get_entity(eid))


def _make_pipeline(db, method_name: str = "belief_diffusion",
                   moderator_only: bool = True):
    """Build an active discussion driven by a human moderator.

    Returns (discussion, moderator_entity, Moderator, PricingCache).
    """
    mod = _entity(db, "Mod")
    parts = [_entity(db, "P1"), _entity(db, "P2")]
    disc = Discussion(
        topic="Unframeable question",
        entities=[mod] + parts,
        moderator_id=mod.id,
        turn_order=[mod.id] if moderator_only else [p.id for p in parts],
        base_turn_order=[p.id for p in parts],
        current_turn_index=0,
        turn_number=1,
        is_active=True,
        status="active",
        discussion_method=method_name,
    )
    disc.id = db.create_discussion(disc.topic, mod.id)
    from consensus.methods import get_method
    disc.method_state = get_method(method_name).init_state(disc)
    moderator = Moderator(disc, db)
    pricing = PricingCache(db.conn, db._lock)
    return disc, mod, moderator, pricing


def _warning_msgs(disc):
    """System messages announcing the framing give-up."""
    return [m for m in disc.messages
            if m.role == MessageRole.SYSTEM
            and "framing" in m.content.lower()
            and "hypothes" in m.content.lower()]


async def _drive_framing_turn(disc, mod, moderator, db, pricing):
    """One unparseable moderator framing turn through the real pipeline."""
    submitted = submit_human_message(
        disc, db, mod.id,
        "I am sorry, I cannot think of any hypotheses here.",
    )
    assert "error" not in submitted
    result = await complete_turn(
        disc, moderator, db, pricing,
        get_state_fn=lambda: {},
        moderator_summary="Noted.",
    )
    assert "error" not in result
    return result


class TestAbortThroughPipeline:
    """Drive complete_turn with a human moderator whose framing responses
    never contain a parseable numbered list."""

    @pytest.mark.asyncio
    async def test_exhausted_framing_completes_method_with_warning(
        self, tmp_db,
    ):
        disc, mod, moderator, pricing = _make_pipeline(tmp_db)

        method_complete = False
        for _ in range(MAX_FRAMING_ATTEMPTS):
            result = await _drive_framing_turn(
                disc, mod, moderator, tmp_db, pricing)
            if result.get("method_complete"):
                method_complete = True
                break

        assert method_complete, "method did not end after framing gave up"
        # Prior phase must never have started.
        assert disc.method_state["current_phase"] == "frame"
        assert disc.method_state["hypotheses"] == []

        # A clear user-facing system message must have been posted.
        assert _warning_msgs(disc)

    @pytest.mark.asyncio
    async def test_complete_message_posted_only_once(self, tmp_db):
        """Turns completing after method_complete must not repost the
        warning — complete_turn re-enters the method-ended branch on
        every subsequent turn while the frontend concludes."""
        disc, mod, moderator, pricing = _make_pipeline(tmp_db)

        for _ in range(MAX_FRAMING_ATTEMPTS):
            result = await _drive_framing_turn(
                disc, mod, moderator, tmp_db, pricing)
        assert result.get("method_complete") is True
        assert len(_warning_msgs(disc)) == 1

        # One more turn completes before the frontend concludes.
        result = await _drive_framing_turn(
            disc, mod, moderator, tmp_db, pricing)
        assert result.get("method_complete") is True
        assert len(_warning_msgs(disc)) == 1, (
            "the method-complete warning was posted more than once"
        )


class TestTriageSwitchPostsNoEndMessage:
    """Triage handing off to its chosen method is a switch, not an end —
    a method-complete message must not be posted."""

    @pytest.mark.asyncio
    async def test_no_end_message_on_triage_switch(self, tmp_db, monkeypatch):
        from consensus.methods.triage import TriageMethod
        monkeypatch.setattr(
            TriageMethod, "get_method_complete_message",
            lambda self, d: "TRIAGE END MESSAGE",
        )
        disc, mod, moderator, pricing = _make_pipeline(
            tmp_db, method_name="triage")
        disc.method_state["current_phase"] = "confirm"
        # The confirm handler re-parses the moderator's message; the
        # recommended_method fallback keeps the choice deterministic.
        disc.method_state["chosen_method"] = "delphi"
        disc.method_state["recommended_method"] = "delphi"
        disc.turn_number = 5

        result = await _drive_framing_turn(
            disc, mod, moderator, tmp_db, pricing)

        assert result.get("method_switched") is True
        assert not any("TRIAGE END MESSAGE" in m.content
                       for m in disc.messages)
