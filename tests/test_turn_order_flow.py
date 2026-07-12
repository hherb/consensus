"""Regression tests for method turn-order handling in the discussion flow.

Covers GitHub issue #13: phase-transition turn-order narrowing must not
cascade — each phase's ``get_turn_order`` must derive from the full
eligible roster, never from the previous phase's (possibly narrowed)
order.  Also covers applying the first phase's turn order at discussion
start and the empty-order guard.
"""

import pytest

from consensus.app_discussion_flow import complete_turn
from consensus.app_discussion_setup import start_discussion
from consensus.methods import get_method
from consensus.moderator import Moderator
from consensus.models import Discussion, Entity, EntityType
from consensus.pricing import PricingCache


def _entity(db, name: str, entity_type: str = "human") -> Entity:
    """Insert a human entity and return the loaded Entity."""
    eid = db.add_entity(name, entity_type, "#123456")
    return Entity.from_db_row(db.get_entity(eid))


def _make_discussion(db, method_name: str, n_participants: int = 3):
    """Build an active discussion with a human moderator and participants.

    Returns (discussion, moderator_entity, participant_entities).
    The turn order contains only the participants (moderator does not
    participate), mirroring start_discussion's default behavior.
    """
    mod = _entity(db, "Mod")
    participants = [
        _entity(db, f"P{i + 1}") for i in range(n_participants)
    ]
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
    method = get_method(method_name)
    disc.method_state = method.init_state(disc)
    return disc, mod, participants


async def _run_complete_turn(disc, db):
    """Drive complete_turn through the human-moderator summary path."""
    moderator = Moderator(disc, db)
    pricing = PricingCache(db.conn, db._lock)
    return await complete_turn(
        disc, moderator, db, pricing,
        get_state_fn=lambda: {},
        moderator_summary="Summary of the turn.",
    )


class TestPhaseTransitionTurnOrder:
    """Narrowed phase orders must not cascade into the next phase."""

    @pytest.mark.asyncio
    async def test_counterfactual_stress_phase_has_participants(self, tmp_db):
        """extract (moderator-only) -> stress_test must yield participants.

        Regression: stress_test received the narrowed [moderator] order,
        filtered the moderator out, and installed an empty turn order,
        permanently stalling the discussion (issue #13).
        """
        disc, mod, parts = _make_discussion(tmp_db, "counterfactual")
        # Simulate: extract phase ran (moderator-only order) and claims
        # were successfully extracted.
        disc.method_state["current_phase"] = "extract"
        disc.method_state["claims"] = [{"id": 1, "text": "A testable claim"}]
        disc.turn_order = [mod.id]
        disc.current_turn_index = 0
        disc.turn_number = 5  # past turn 1 so round-wrap detection fires

        result = await _run_complete_turn(disc, tmp_db)

        assert "error" not in result
        assert disc.method_state["current_phase"] == "stress_test"
        assert disc.turn_order, "turn order must never become empty"
        assert disc.turn_order == [p.id for p in parts]
        assert disc.current_speaker is not None

    @pytest.mark.asyncio
    async def test_self_distillation_blind_evaluate_has_participants(
        self, tmp_db,
    ):
        """distill (moderator-only) -> blind_evaluate must yield participants."""
        disc, mod, parts = _make_discussion(tmp_db, "self_distillation")
        disc.method_state["current_phase"] = "distill"
        # A parsed skeleton lets the distill phase advance.
        disc.method_state["skeleton"] = {
            "premises": [{"id": "P1", "text": "premise"}],
            "inferences": [{"id": "I1", "text": "inference"}],
            "conclusions": [{"id": "C1", "text": "conclusion"}],
        }
        disc.turn_order = [mod.id]
        disc.current_turn_index = 0
        disc.turn_number = 5

        result = await _run_complete_turn(disc, tmp_db)

        assert "error" not in result
        assert disc.turn_order, "turn order must never become empty"
        assert disc.turn_order == [p.id for p in parts]

    @pytest.mark.asyncio
    async def test_red_team_attacker_leads_attack_phase(self, tmp_db):
        """construct (blue-only) -> attack must put the red team first.

        Regression: attack received the blue-only order, did not find the
        red id in it, and silently ran the attack phase with no attacker.
        """
        disc, mod, parts = _make_discussion(tmp_db, "red_team")
        red, blue1, blue2 = parts
        disc.method_state["current_phase"] = "construct"
        disc.method_state["red_team_entity_id"] = red.id
        disc.turn_order = [blue1.id, blue2.id]
        disc.current_turn_index = 1  # last blue member just spoke
        disc.turn_number = 3

        result = await _run_complete_turn(disc, tmp_db)

        assert "error" not in result
        assert disc.method_state["current_phase"] == "attack"
        assert red.id in disc.turn_order, "red team must be in attack order"
        assert disc.turn_order[0] == red.id, "red team attacks first"

    @pytest.mark.asyncio
    async def test_triage_switch_seeds_full_roster(self, tmp_db):
        """Triage exhaustion must seed the chosen method with all participants.

        Regression: the confirm phase's [moderator] order was passed to the
        new method, so the switched-to discussion ran moderator-only.
        """
        disc, mod, parts = _make_discussion(tmp_db, "triage")
        disc.method_state["current_phase"] = "confirm"
        disc.method_state["chosen_method"] = "delphi"
        disc.turn_order = [mod.id]
        disc.current_turn_index = 0
        disc.turn_number = 5

        result = await _run_complete_turn(disc, tmp_db)

        assert result.get("method_switched") is True
        assert disc.discussion_method == "delphi"
        assert disc.turn_order == [p.id for p in parts]


class TestStartDiscussionTurnOrder:
    """The first phase's turn order must apply from turn 1."""

    def test_red_team_excluded_from_construction_at_start(self, tmp_db):
        """Red team must be designated and excluded before round 1.

        Regression: get_turn_order was never applied at discussion start,
        so the future red team participated in construction round 1.
        """
        mod = _entity(tmp_db, "Mod")
        parts = [_entity(tmp_db, f"P{i + 1}") for i in range(3)]
        disc = Discussion(
            topic="Test topic",
            entities=[mod] + parts,
            moderator_id=mod.id,
            discussion_method="red_team",
        )
        moderator = Moderator(disc, tmp_db)

        result = start_discussion(disc, tmp_db, moderator)

        assert result.get("started") is True
        assert disc.base_turn_order == [p.id for p in parts]
        red_id = disc.method_state.get("red_team_entity_id")
        assert red_id == parts[0].id
        assert disc.turn_order == [p.id for p in parts[1:]]

    def test_empty_first_phase_order_keeps_roster(self, tmp_db, sample_provider):
        """A first-phase order of [] must not be installed.

        Triage intake is humans-only; with AI-only participants it returns
        an empty list, which must fall back to the full roster.
        """
        mod = _entity(tmp_db, "Mod")
        ai_ids = [
            tmp_db.add_entity(f"AI{i}", "ai", "#654321", sample_provider,
                              "test-model", 0.5, 512, "You are an AI.")
            for i in range(2)
        ]
        ais = [Entity.from_db_row(tmp_db.get_entity(eid)) for eid in ai_ids]
        disc = Discussion(
            topic="Test topic",
            entities=[mod] + ais,
            moderator_id=mod.id,
            discussion_method="triage",
        )
        moderator = Moderator(disc, tmp_db)

        result = start_discussion(disc, tmp_db, moderator)

        assert result.get("started") is True
        assert disc.turn_order == [a.id for a in ais]
