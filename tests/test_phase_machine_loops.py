"""Tests for phase-machine loop support (issue #22).

``advance_phase`` historically walked forward through a fixed tuple of
phases.  The ``next_phase`` hook lets a method (or its active
PhaseHandler) choose the next phase by name instead: jump backwards to
loop, or return ``None`` to end the method early.  A loop guard caps the
total number of phase transitions so a buggy hook cannot cycle forever.
"""

import logging

import pytest

import consensus.methods as methods_registry
from consensus.app_discussion_flow import complete_turn
from consensus.methods.base import (
    LINEAR_NEXT,
    MAX_PHASE_VISITS_PER_PHASE,
    DiscussionMethod,
    Phase,
)
from consensus.methods.phase_handler import PhaseHandler
from consensus.moderator import Moderator
from consensus.models import Discussion, Entity
from consensus.pricing import PricingCache


# ---------------------------------------------------------------------------
# Toy methods
# ---------------------------------------------------------------------------


class LinearMethod(DiscussionMethod):
    """Plain three-phase method with no hook overrides."""

    name = "_test_linear"
    display_name = "Linear Test"
    description = "test"
    default_phases = (
        Phase("a", "Phase A"),
        Phase("b", "Phase B"),
        Phase("c", "Phase C"),
    )


class LoopOnceMethod(DiscussionMethod):
    """converge loops back to diverge exactly once, then runs linearly."""

    name = "_test_loop_once"
    display_name = "Loop Once Test"
    description = "test"
    default_phases = (
        Phase("diverge", "Diverge"),
        Phase("converge", "Converge"),
    )

    def next_phase(self, discussion: Discussion) -> str | None:
        if (discussion.method_state.get("current_phase") == "converge"
                and not discussion.method_state.get("looped")):
            discussion.method_state["looped"] = True
            return "diverge"
        return super().next_phase(discussion)


class AbortMethod(DiscussionMethod):
    """Ends the method from the very first phase."""

    name = "_test_abort"
    display_name = "Abort Test"
    description = "test"
    default_phases = (
        Phase("a", "Phase A"),
        Phase("b", "Phase B"),
    )

    def next_phase(self, discussion: Discussion) -> str | None:
        return None


class BadNameMethod(DiscussionMethod):
    """Returns a phase name that does not exist."""

    name = "_test_bad_name"
    display_name = "Bad Name Test"
    description = "test"
    default_phases = (
        Phase("a", "Phase A"),
        Phase("b", "Phase B"),
    )

    def next_phase(self, discussion: Discussion) -> str | None:
        return "no_such_phase"


class SentinelMethod(DiscussionMethod):
    """Returns LINEAR_NEXT from a method-level override (instead of
    calling ``super().next_phase(...)``) — must fall back to the linear
    order, not be treated as an unknown phase name."""

    name = "_test_sentinel"
    display_name = "Sentinel Test"
    description = "test"
    default_phases = (
        Phase("a", "Phase A"),
        Phase("b", "Phase B"),
    )

    def next_phase(self, discussion: Discussion) -> str | None:
        return LINEAR_NEXT


class ForeverMethod(DiscussionMethod):
    """Always loops back to the first phase — must hit the loop guard."""

    name = "_test_forever"
    display_name = "Forever Test"
    description = "test"
    default_phases = (
        Phase("a", "Phase A"),
        Phase("b", "Phase B"),
    )

    def next_phase(self, discussion: Discussion) -> str | None:
        return "a"


class CappedForeverMethod(ForeverMethod):
    """Same runaway loop, but with an explicit per-method cap."""

    name = "_test_capped_forever"
    max_phase_entries = 3


# ---------------------------------------------------------------------------
# Toy handlers (handler-level delegation)
# ---------------------------------------------------------------------------


class _StubHandler(PhaseHandler):
    """Minimal concrete handler."""

    phase = Phase("a", "Phase A")

    def get_system_prompt(self, entity: Entity,
                          discussion: Discussion) -> str:
        return ""

    def get_turn_prompt(self, entity: Entity,
                        discussion: Discussion) -> str:
        return ""


class _LoopingHandler(_StubHandler):
    """Handler that jumps back to phase "a" once, then defers to linear."""

    phase = Phase("b", "Phase B")

    def next_phase(self, discussion: Discussion) -> str | None:
        if not discussion.method_state.get("handler_looped"):
            discussion.method_state["handler_looped"] = True
            return "a"
        return LINEAR_NEXT


class _AbortingHandler(_StubHandler):
    """Handler that ends the method from its phase."""

    phase = Phase("a", "Phase A")

    def next_phase(self, discussion: Discussion) -> str | None:
        return None


class _FinalHandler(_StubHandler):
    phase = Phase("c", "Phase C")


class HandlerLoopMethod(DiscussionMethod):
    name = "_test_handler_loop"
    display_name = "Handler Loop Test"
    description = "test"
    phase_handlers = (_StubHandler(), _LoopingHandler(), _FinalHandler())


class HandlerAbortMethod(DiscussionMethod):
    name = "_test_handler_abort"
    display_name = "Handler Abort Test"
    description = "test"
    phase_handlers = (_AbortingHandler(), _LoopingHandler())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _discussion(method: DiscussionMethod) -> Discussion:
    disc = Discussion(topic="t", discussion_method=method.name)
    disc.method_state = method.init_state(disc)
    return disc


# ---------------------------------------------------------------------------
# Linear default (behavior must be unchanged)
# ---------------------------------------------------------------------------


class TestLinearDefault:
    def test_next_phase_returns_successor_name(self):
        m = LinearMethod()
        disc = _discussion(m)
        assert m.next_phase(disc) == "b"

    def test_next_phase_returns_none_after_last_phase(self):
        m = LinearMethod()
        disc = _discussion(m)
        disc.method_state["current_phase"] = "c"
        assert m.next_phase(disc) is None

    def test_advance_phase_walks_linearly_and_resets_round(self):
        m = LinearMethod()
        disc = _discussion(m)
        disc.method_state["phase_round"] = 4

        new_phase = m.advance_phase(disc)
        assert new_phase is not None and new_phase.name == "b"
        assert disc.method_state["current_phase"] == "b"
        assert disc.method_state["phase_round"] == 1

        new_phase = m.advance_phase(disc)
        assert new_phase is not None and new_phase.name == "c"
        assert m.advance_phase(disc) is None

    def test_linear_walk_never_trips_loop_guard(self):
        m = LinearMethod()
        disc = _discussion(m)
        assert m.advance_phase(disc) is not None
        assert m.advance_phase(disc) is not None
        assert disc.method_state["current_phase"] == "c"


# ---------------------------------------------------------------------------
# Method-level looping, aborting, and bad names
# ---------------------------------------------------------------------------


class TestMethodLevelHook:
    def test_loops_back_once_then_completes(self):
        m = LoopOnceMethod()
        disc = _discussion(m)

        visited = []
        while (phase := m.advance_phase(disc)) is not None:
            visited.append(phase.name)

        assert visited == ["converge", "diverge", "converge"]

    def test_loop_back_resets_phase_round(self):
        m = LoopOnceMethod()
        disc = _discussion(m)
        disc.method_state["current_phase"] = "converge"
        disc.method_state["phase_round"] = 3

        new_phase = m.advance_phase(disc)
        assert new_phase is not None and new_phase.name == "diverge"
        assert disc.method_state["phase_round"] == 1

    def test_next_phase_none_ends_method_early(self):
        m = AbortMethod()
        disc = _discussion(m)
        assert m.advance_phase(disc) is None
        # State untouched — still in the first phase.
        assert disc.method_state["current_phase"] == "a"

    def test_linear_sentinel_from_method_hook_falls_back_to_linear(self):
        m = SentinelMethod()
        disc = _discussion(m)

        new_phase = m.advance_phase(disc)
        assert new_phase is not None and new_phase.name == "b"
        assert m.advance_phase(disc) is None  # linear order exhausted

    def test_unknown_phase_name_ends_method_with_warning(self, caplog):
        m = BadNameMethod()
        disc = _discussion(m)
        with caplog.at_level(logging.WARNING):
            assert m.advance_phase(disc) is None
        assert "no_such_phase" in caplog.text


# ---------------------------------------------------------------------------
# Loop guard
# ---------------------------------------------------------------------------


class TestLoopGuard:
    def test_runaway_loop_terminates_at_cap(self):
        m = ForeverMethod()
        disc = _discussion(m)
        cap = len(m.default_phases) * MAX_PHASE_VISITS_PER_PHASE

        transitions = 0
        while m.advance_phase(disc) is not None:
            transitions += 1
            assert transitions <= cap, "loop guard failed to stop a cycle"
        assert transitions == cap

    def test_explicit_max_phase_entries_overrides_default_cap(self):
        m = CappedForeverMethod()
        disc = _discussion(m)

        transitions = 0
        while m.advance_phase(disc) is not None:
            transitions += 1
            assert transitions <= CappedForeverMethod.max_phase_entries
        assert transitions == CappedForeverMethod.max_phase_entries

    def test_transition_count_is_persisted_in_method_state(self):
        m = LinearMethod()
        disc = _discussion(m)
        m.advance_phase(disc)
        assert disc.method_state["_phase_entries"] == 1


# ---------------------------------------------------------------------------
# Handler-level delegation
# ---------------------------------------------------------------------------


class TestHandlerDelegation:
    def test_handler_jump_overrides_linear_order(self):
        m = HandlerLoopMethod()
        disc = _discussion(m)
        disc.method_state["current_phase"] = "b"

        new_phase = m.advance_phase(disc)
        assert new_phase is not None and new_phase.name == "a"

    def test_handler_linear_sentinel_defers_to_default_order(self):
        m = HandlerLoopMethod()
        disc = _discussion(m)
        disc.method_state["current_phase"] = "b"
        disc.method_state["handler_looped"] = True

        new_phase = m.advance_phase(disc)
        assert new_phase is not None and new_phase.name == "c"

    def test_handler_none_aborts_method(self):
        m = HandlerAbortMethod()
        disc = _discussion(m)
        assert m.advance_phase(disc) is None

    def test_base_handler_default_is_linear_sentinel(self):
        disc = Discussion(topic="t")
        assert _StubHandler().next_phase(disc) == LINEAR_NEXT


# ---------------------------------------------------------------------------
# Integration: looping method driven through the real pipeline
# ---------------------------------------------------------------------------


class DivergeConvergeLoop(DiscussionMethod):
    """One diverge→converge cycle repeated twice, via next_phase."""

    name = "_test_integration_loop"
    display_name = "Integration Loop Test"
    description = "test"
    default_phases = (
        Phase("diverge", "Diverge", rounds=1),
        Phase("converge", "Converge", rounds=1),
    )

    def next_phase(self, discussion: Discussion) -> str | None:
        if (discussion.method_state.get("current_phase") == "converge"
                and discussion.method_state.get("cycles", 0) < 1):
            discussion.method_state["cycles"] = (
                discussion.method_state.get("cycles", 0) + 1
            )
            return "diverge"
        return super().next_phase(discussion)


def _entity(db, name: str) -> Entity:
    eid = db.add_entity(name, "human", "#123456")
    return Entity.from_db_row(db.get_entity(eid))


class TestLoopThroughPipeline:
    """Drive complete_turn with a looping method and a human moderator."""

    @pytest.mark.asyncio
    async def test_full_loop_cycle_then_method_complete(
        self, tmp_db, monkeypatch,
    ):
        method = DivergeConvergeLoop()
        monkeypatch.setitem(
            methods_registry._METHODS, method.name, DivergeConvergeLoop)
        monkeypatch.setitem(
            methods_registry._INSTANCES, method.name, method)

        mod = _entity(tmp_db, "Mod")
        parts = [_entity(tmp_db, "P1"), _entity(tmp_db, "P2")]
        disc = Discussion(
            topic="Loop test",
            entities=[mod] + parts,
            moderator_id=mod.id,
            turn_order=[p.id for p in parts],
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

        phases_seen = [disc.method_state["current_phase"]]
        method_complete = False
        # 2 participants × 1 round × 4 phase entries = 8 turns max.
        for _ in range(10):
            result = await complete_turn(
                disc, moderator, tmp_db, pricing,
                get_state_fn=lambda: {},
                moderator_summary="Summary of the turn.",
            )
            assert "error" not in result
            if disc.method_state["current_phase"] != phases_seen[-1]:
                phases_seen.append(disc.method_state["current_phase"])
            if result.get("method_complete"):
                method_complete = True
                break

        assert method_complete, "looping method never completed"
        assert phases_seen == ["diverge", "converge", "diverge", "converge"]
        # 3 transitions: →converge, →diverge (loop), →converge.
        assert disc.method_state["_phase_entries"] == 3
