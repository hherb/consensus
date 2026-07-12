"""Regression tests for method-state persistence and resume (GitHub issue #16).

Round-lifecycle mutations (phase_round, huddle sub-state), method-driven
turn orders, and the turn counter must survive a crash/reload between a
round completion and the next AI turn.
"""

import json

import pytest

from consensus.app_discussion_flow import (
    complete_turn,
    submit_human_message,
    switch_discussion_method,
)
from consensus.app_discussion_state import load_discussion
from consensus.methods import get_method
from consensus.moderator import Moderator
from consensus.models import Discussion, Entity
from consensus.pricing import PricingCache


def _entity(db, name: str) -> Entity:
    eid = db.add_entity(name, "human", "#123456")
    return Entity.from_db_row(db.get_entity(eid))


def _make_db_discussion(db, method_name: str, n_participants: int = 2):
    """Active discussion with DB-backed members (for load_discussion)."""
    mod = _entity(db, "Mod")
    participants = [_entity(db, f"P{i + 1}") for i in range(n_participants)]
    disc = Discussion(
        topic="Test topic",
        entities=[mod] + participants,
        moderator_id=mod.id,
        turn_order=[p.id for p in participants],
        base_turn_order=[p.id for p in participants],
        current_turn_index=0,
        turn_number=1,
        is_active=True,
        status="active",
        discussion_method=method_name,
    )
    disc.id = db.create_discussion(disc.topic, mod.id)
    db.update_discussion(disc.id, status="active",
                         discussion_method=method_name)
    db.add_discussion_member(disc.id, mod.id, is_moderator=True,
                             also_participant=False, turn_position=None)
    for pos, p in enumerate(participants):
        db.add_discussion_member(disc.id, p.id, is_moderator=False,
                                 also_participant=True, turn_position=pos)
    method = get_method(method_name)
    disc.method_state = method.init_state(disc)
    return disc, mod, participants


async def _run_complete_turn(disc, db):
    moderator = Moderator(disc, db)
    pricing = PricingCache(db.conn, db._lock)
    return await complete_turn(
        disc, moderator, db, pricing,
        get_state_fn=lambda: {},
        moderator_summary="Summary of the turn.",
    )


def _db_method_state(db, discussion_id: int) -> dict:
    row = db.get_discussion(discussion_id)
    raw = row.get("method_state") or "{}"
    return json.loads(raw)


class TestRoundStatePersistence:
    """phase_round increments must be persisted without a phase transition."""

    @pytest.mark.asyncio
    async def test_phase_round_persisted_mid_phase(self, tmp_db):
        disc, mod, parts = _make_db_discussion(tmp_db, "counterfactual")
        # cf_deliberate has rounds=2: after round 1 completes there is no
        # transition, but the incremented phase_round must reach the DB.
        disc.method_state["current_phase"] = "cf_deliberate"
        disc.current_turn_index = 1  # last participant just spoke
        disc.turn_number = 2

        await _run_complete_turn(disc, tmp_db)

        assert disc.method_state["phase_round"] == 2
        persisted = _db_method_state(tmp_db, disc.id)
        assert persisted.get("phase_round") == 2, (
            "phase_round must be persisted at round completion, not lazily"
        )


class TestTurnOrderPersistence:
    """Method-narrowed turn orders must survive a reload."""

    @pytest.mark.asyncio
    async def test_narrowed_order_restored_by_load_discussion(self, tmp_db):
        disc, mod, parts = _make_db_discussion(tmp_db, "counterfactual")
        # Force the cf_deliberate -> extract transition (moderator-only).
        disc.method_state["current_phase"] = "cf_deliberate"
        disc.method_state["phase_round"] = 2  # final deliberation round
        disc.current_turn_index = 1
        disc.turn_number = 4

        await _run_complete_turn(disc, tmp_db)
        assert disc.method_state["current_phase"] == "extract"
        assert disc.turn_order == [mod.id]

        loaded = load_discussion(
            tmp_db, disc.id, key_resolver=lambda pid, env: "", tool_registry=None,
        )
        assert not isinstance(loaded, dict), f"load failed: {loaded}"
        restored, _moderator = loaded
        assert restored.turn_order == [mod.id], (
            "reload must restore the phase-narrowed turn order, not the roster"
        )
        assert restored.base_turn_order == [p.id for p in parts]


class TestTurnNumberRestore:
    """load_discussion must restore turn_number = max(turn) + 1."""

    def test_turn_number_is_max_plus_one(self, tmp_db):
        disc, mod, parts = _make_db_discussion(tmp_db, "open_discussion")
        for turn in (1, 2, 3):
            tmp_db.add_message(
                disc.id, parts[turn % len(parts)].id,
                f"message {turn}", "participant", turn_number=turn,
            )

        loaded = load_discussion(
            tmp_db, disc.id, key_resolver=lambda pid, env: "", tool_registry=None,
        )
        assert not isinstance(loaded, dict)
        restored, _moderator = loaded
        assert restored.turn_number == 4


class TestHumanMessageMethodProcessing:
    """Human responses must go through method.process_response and persist."""

    def test_human_estimate_recorded_and_persisted(self, tmp_db):
        disc, mod, parts = _make_db_discussion(tmp_db, "delphi")
        disc.method_state["current_phase"] = "estimate"
        human = parts[0]
        disc.current_turn_index = 0

        result = submit_human_message(
            disc, tmp_db, human.id,
            'My estimate:\n```json\n{"estimate": 0.7, '
            '"confidence": "HIGH", "unit": "probability"}\n```',
        )

        assert "error" not in result
        estimates = disc.method_state.get("estimates", [])
        assert estimates and estimates[0]["value"] == 0.7, (
            "human responses must be processed by the method"
        )
        persisted = _db_method_state(tmp_db, disc.id)
        assert persisted.get("estimates"), (
            "method_state must be persisted after a human message"
        )


class TestMethodSwitchPreservesBudgetKeys:
    """switch_discussion_method must not wipe budget bookkeeping."""

    def test_budget_keys_survive_switch(self, tmp_db):
        disc, mod, parts = _make_db_discussion(tmp_db, "triage")
        disc.method_state["_continuation_count"] = 3
        disc.method_state["_original_max_rounds"] = 10
        disc.method_state["_original_cost_limit"] = 2.5

        result = switch_discussion_method(disc, tmp_db, "delphi")

        assert "error" not in result
        assert disc.method_state["_continuation_count"] == 3
        assert disc.method_state["_original_max_rounds"] == 10
        assert disc.method_state["_original_cost_limit"] == 2.5
