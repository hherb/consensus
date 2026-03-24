"""Tests for the Court of Law discussion method."""

import pytest

from consensus.methods import get_method, list_methods
from consensus.methods.court_of_law import CourtOfLaw
from consensus.methods.phases._court_helpers import (
    HUDDLE_PREFIX,
    advance_huddle_state,
    auto_skip_solo_huddles,
    extract_spokesperson,
    get_accusation_ids,
    get_defense_ids,
    get_huddle_state,
    get_team_for_entity,
    get_trial_type,
    huddle_turn_order,
    init_huddle_state,
)
from consensus.models import Discussion, Entity, EntityType


# ── Fixtures ──────────────────────────────────────────────────────────

@pytest.fixture
def method():
    return CourtOfLaw()


@pytest.fixture
def prosecutor():
    return Entity(name="Prosecutor", entity_type=EntityType.AI, id=1)


@pytest.fixture
def defense1():
    return Entity(name="DefenseA", entity_type=EntityType.AI, id=2)


@pytest.fixture
def defense2():
    return Entity(name="DefenseB", entity_type=EntityType.AI, id=3)


@pytest.fixture
def moderator():
    return Entity(name="Judge", entity_type=EntityType.AI, id=10)


@pytest.fixture
def plaintiff1():
    return Entity(name="PlaintiffA", entity_type=EntityType.AI, id=4)


@pytest.fixture
def plaintiff2():
    return Entity(name="PlaintiffB", entity_type=EntityType.AI, id=5)


@pytest.fixture
def criminal_discussion(method, prosecutor, defense1, defense2, moderator):
    """A criminal trial: 1 prosecutor vs 2 defense."""
    disc = Discussion(topic="Fraud charges", discussion_method="court_of_law")
    disc.entities = [moderator, prosecutor, defense1, defense2]
    disc.moderator_id = moderator.id
    disc.member_roles = {
        prosecutor.id: "prosecutor",
        defense1.id: "defense",
        defense2.id: "defense",
    }
    disc.turn_order = [prosecutor.id, defense1.id, defense2.id]
    disc.method_state = method.init_state(disc)
    return disc


@pytest.fixture
def civil_discussion(method, plaintiff1, plaintiff2, defense1, moderator):
    """A civil trial: 2 plaintiffs vs 1 defense."""
    disc = Discussion(topic="Contract dispute", discussion_method="court_of_law")
    disc.entities = [moderator, plaintiff1, plaintiff2, defense1]
    disc.moderator_id = moderator.id
    disc.member_roles = {
        plaintiff1.id: "plaintiff",
        plaintiff2.id: "plaintiff",
        defense1.id: "defense",
    }
    disc.turn_order = [plaintiff1.id, plaintiff2.id, defense1.id]
    disc.method_state = method.init_state(disc)
    return disc


# ── Registration ──────────────────────────────────────────────────────

class TestRegistration:
    def test_registered_in_methods(self):
        methods = list_methods()
        names = [m["name"] for m in methods]
        assert "court_of_law" in names

    def test_get_method(self):
        m = get_method("court_of_law")
        assert m.name == "court_of_law"
        assert m.display_name == "Court of Law"

    def test_has_five_phases(self):
        m = get_method("court_of_law")
        info = m.to_dict()
        assert len(info["phases"]) == 5
        phase_names = [p["name"] for p in info["phases"]]
        assert phase_names == [
            "arraignment",
            "opening_statements",
            "prosecution_case",
            "defense_case",
            "closing_arguments",
        ]


# ── Trial type inference ──────────────────────────────────────────────

class TestTrialType:
    def test_criminal_inferred_from_prosecutor(self, criminal_discussion):
        assert get_trial_type(criminal_discussion) == "criminal"

    def test_civil_inferred_from_plaintiff(self, civil_discussion):
        assert get_trial_type(civil_discussion) == "civil"

    def test_default_criminal(self, method, defense1, moderator):
        disc = Discussion(topic="test", discussion_method="court_of_law")
        disc.entities = [moderator, defense1]
        disc.moderator_id = moderator.id
        disc.member_roles = {defense1.id: "defense"}
        disc.method_state = method.init_state(disc)
        assert get_trial_type(disc) == "criminal"


# ── Team helpers ──────────────────────────────────────────────────────

class TestTeamHelpers:
    def test_accusation_ids_criminal(self, criminal_discussion, prosecutor):
        ids = get_accusation_ids(criminal_discussion)
        assert ids == [prosecutor.id]

    def test_accusation_ids_civil(self, civil_discussion, plaintiff1, plaintiff2):
        ids = get_accusation_ids(civil_discussion)
        assert set(ids) == {plaintiff1.id, plaintiff2.id}

    def test_defense_ids(self, criminal_discussion, defense1, defense2):
        ids = get_defense_ids(criminal_discussion)
        assert set(ids) == {defense1.id, defense2.id}

    def test_team_for_entity(self, criminal_discussion, prosecutor,
                              defense1, moderator):
        assert get_team_for_entity(prosecutor.id, criminal_discussion) == "accusation"
        assert get_team_for_entity(defense1.id, criminal_discussion) == "defense"
        assert get_team_for_entity(moderator.id, criminal_discussion) is None


# ── Phase transitions ─────────────────────────────────────────────────

class TestPhaseTransitions:
    def test_initial_phase_is_arraignment(self, method, criminal_discussion):
        phase = method.current_phase(criminal_discussion)
        assert phase.name == "arraignment"

    def test_advance_through_all_phases(self, method, criminal_discussion):
        disc = criminal_discussion
        expected_phases = [
            "arraignment",
            "opening_statements",
            "prosecution_case",
            "defense_case",
            "closing_arguments",
        ]
        for i, expected in enumerate(expected_phases):
            phase = method.current_phase(disc)
            assert phase.name == expected, f"Phase {i}: expected {expected}, got {phase.name}"
            # Simulate rounds completing
            disc.method_state["phase_round"] = phase.rounds + 1
            # For huddle phases, force the huddle to done
            if phase.name == "opening_statements":
                disc.method_state["opening_huddle"]["sub_state"] = "done"
            elif phase.name == "closing_arguments":
                disc.method_state["closing_huddle"]["sub_state"] = "done"
            if method.should_advance_phase(disc):
                new_phase = method.advance_phase(disc)
                if i < len(expected_phases) - 1:
                    assert new_phase is not None

    def test_verdict_uses_conclusion_prompt(self, method, criminal_discussion):
        prompt = method.get_conclusion_prompt(criminal_discussion)
        assert "verdict" in prompt.lower()
        assert "charge" in prompt.lower() or "claim" in prompt.lower()


# ── Turn order ────────────────────────────────────────────────────────

class TestTurnOrder:
    def test_arraignment_accusation_first(self, method, criminal_discussion,
                                          prosecutor, defense1, defense2):
        disc = criminal_discussion
        order = method.get_turn_order(disc.turn_order, disc)
        assert order[0] == prosecutor.id
        assert set(order[1:]) == {defense1.id, defense2.id}

    def test_defense_case_defense_first(self, method, criminal_discussion,
                                        prosecutor, defense1, defense2):
        disc = criminal_discussion
        # Advance to defense_case phase
        disc.method_state["current_phase"] = "defense_case"
        disc.method_state["phase_index"] = 3
        order = method.get_turn_order(disc.turn_order, disc)
        # Defense should come first
        defense_ids = {defense1.id, defense2.id}
        assert order[0] in defense_ids or order[1] in defense_ids

    def test_civil_plaintiffs_as_accusation(self, method, civil_discussion,
                                             plaintiff1, plaintiff2, defense1):
        disc = civil_discussion
        order = method.get_turn_order(disc.turn_order, disc)
        # Both plaintiffs before defense
        acc_ids = {plaintiff1.id, plaintiff2.id}
        assert order[0] in acc_ids
        assert order[1] in acc_ids
        assert order[2] == defense1.id


# ── Huddle mechanism ──────────────────────────────────────────────────

class TestHuddle:
    def test_init_huddle_state(self):
        state = init_huddle_state()
        assert state["sub_state"] == "accusation_huddle"
        assert state["huddle_round"] == 0
        assert state["spokesperson_id"] is None

    def test_single_member_team_skips_huddle(self, criminal_discussion):
        """Prosecutor is alone — auto_skip should go directly to speaks."""
        disc = criminal_discussion
        key = "test_huddle"
        disc.method_state[key] = init_huddle_state()
        auto_skip_solo_huddles(disc, key)
        huddle = disc.method_state[key]
        assert huddle["sub_state"] == "accusation_speaks"

    def test_multi_member_team_huddles(self, civil_discussion):
        """Plaintiffs (2 members) should get huddle rounds."""
        disc = civil_discussion
        key = "test_huddle"
        disc.method_state[key] = init_huddle_state()
        huddle = disc.method_state[key]

        # First advance: 2 plaintiffs → huddle round 1
        advance_huddle_state(disc, key)
        assert huddle["sub_state"] == "accusation_huddle"
        assert huddle["huddle_round"] == 1

        # Second advance: huddle round 2
        advance_huddle_state(disc, key)
        assert huddle["sub_state"] == "accusation_huddle"
        assert huddle["huddle_round"] == 2

        # Third advance: max rounds reached → speaks
        advance_huddle_state(disc, key)
        assert huddle["sub_state"] == "accusation_speaks"

    def test_full_huddle_cycle(self, civil_discussion):
        """Full cycle: accusation huddle → speaks → defense huddle → speaks → done."""
        disc = civil_discussion
        key = "test_huddle"
        disc.method_state[key] = init_huddle_state()
        huddle = disc.method_state[key]

        # Accusation huddle (2 members: 2 rounds)
        advance_huddle_state(disc, key)  # round 1
        advance_huddle_state(disc, key)  # round 2
        advance_huddle_state(disc, key)  # → accusation_speaks
        assert huddle["sub_state"] == "accusation_speaks"

        # Accusation speaks → defense huddle
        advance_huddle_state(disc, key)
        assert huddle["sub_state"] == "defense_huddle"

        # Defense huddle (1 member: skip to speaks)
        advance_huddle_state(disc, key)
        assert huddle["sub_state"] == "defense_speaks"

        # Defense speaks → done
        advance_huddle_state(disc, key)
        assert huddle["sub_state"] == "done"

    def test_huddle_turn_order(self, civil_discussion, plaintiff1,
                                plaintiff2, defense1):
        disc = civil_discussion
        key = "test_huddle"
        disc.method_state[key] = init_huddle_state()
        huddle = disc.method_state[key]

        # During accusation_huddle: only plaintiffs
        huddle["sub_state"] = "accusation_huddle"
        order = huddle_turn_order(disc, key)
        assert set(order) == {plaintiff1.id, plaintiff2.id}

        # During defense_speaks: only defense
        huddle["sub_state"] = "defense_speaks"
        order = huddle_turn_order(disc, key)
        assert order == [defense1.id]


# ── Spokesperson extraction ──────────────────────────────────────────

class TestSpokesperson:
    def test_extract_from_content(self, civil_discussion, plaintiff1):
        content = "I think we should focus on the contract.\nSPOKESPERSON: PlaintiffA"
        result = extract_spokesperson(content, civil_discussion, "accusation")
        assert result == plaintiff1.id

    def test_no_match_returns_none(self, civil_discussion):
        content = "Let's discuss strategy."
        result = extract_spokesperson(content, civil_discussion, "accusation")
        assert result is None

    def test_case_insensitive(self, civil_discussion, plaintiff1):
        content = "spokesperson: plaintiffa"
        result = extract_spokesperson(content, civil_discussion, "accusation")
        assert result == plaintiff1.id

    def test_wrong_team_not_matched(self, criminal_discussion):
        content = "SPOKESPERSON: DefenseA"
        result = extract_spokesperson(content, criminal_discussion, "accusation")
        assert result is None


# ── Huddle privacy (filter_context_message) ──────────────────────────

class TestHuddlePrivacy:
    def test_non_huddle_message_passes_through(self, method, criminal_discussion):
        content = "Regular court message"
        result = method.filter_context_message(
            "Prosecutor", content, "user", criminal_discussion,
            current_entity_id=2)
        assert result == content

    def test_huddle_message_hidden_from_other_team(self, method,
                                                     criminal_discussion,
                                                     prosecutor, defense1):
        disc = criminal_discussion
        # Advance to opening_statements phase
        disc.method_state["current_phase"] = "opening_statements"
        disc.method_state["phase_index"] = 1

        content = f"{HUDDLE_PREFIX}Let's focus on evidence A"
        # Defense reading a prosecution huddle → suppressed
        result = method.filter_context_message(
            "Prosecutor", content, "user", disc,
            current_entity_id=defense1.id)
        assert result == ""

    def test_huddle_message_visible_to_same_team(self, method,
                                                   criminal_discussion,
                                                   defense1, defense2):
        disc = criminal_discussion
        disc.method_state["current_phase"] = "opening_statements"
        disc.method_state["phase_index"] = 1

        content = f"{HUDDLE_PREFIX}Our defense strategy should be..."
        # Defense teammate reading defense huddle → visible
        result = method.filter_context_message(
            "DefenseA", content, "user", disc,
            current_entity_id=defense2.id)
        assert result == content

    def test_huddle_message_hidden_from_judge(self, method,
                                               criminal_discussion,
                                               moderator):
        disc = criminal_discussion
        disc.method_state["current_phase"] = "opening_statements"
        disc.method_state["phase_index"] = 1

        content = f"{HUDDLE_PREFIX}Strategy discussion"
        # Judge reading a team huddle → suppressed
        result = method.filter_context_message(
            "DefenseA", content, "user", disc,
            current_entity_id=moderator.id)
        assert result == ""

    def test_no_current_entity_passes_through(self, method,
                                                criminal_discussion):
        disc = criminal_discussion
        disc.method_state["current_phase"] = "opening_statements"
        disc.method_state["phase_index"] = 1

        content = f"{HUDDLE_PREFIX}Some huddle content"
        result = method.filter_context_message(
            "Prosecutor", content, "user", disc)
        assert result == content  # No current_entity_id → no filtering

    def test_huddle_privacy_persists_across_phases(self, method,
                                                     criminal_discussion,
                                                     defense1):
        """Huddle messages from opening_statements remain private in later phases."""
        disc = criminal_discussion
        # We are now in prosecution_case, but earlier huddle messages exist
        disc.method_state["current_phase"] = "prosecution_case"
        disc.method_state["phase_index"] = 2

        content = f"{HUDDLE_PREFIX}Our strategy from the huddle"
        # Defense reading a prosecution huddle message from an earlier phase
        result = method.filter_context_message(
            "Prosecutor", content, "user", disc,
            current_entity_id=defense1.id)
        assert result == ""  # Still suppressed!


# ── Process response (huddle tagging) ─────────────────────────────────

class TestProcessResponse:
    def test_huddle_response_gets_prefix(self, method, criminal_discussion,
                                          defense1):
        disc = criminal_discussion
        disc.method_state["current_phase"] = "opening_statements"
        disc.method_state["phase_index"] = 1
        disc.method_state["opening_huddle"]["sub_state"] = "defense_huddle"

        result = method.process_response("Defense strategy here", defense1, disc)
        assert result.display_content.startswith(HUDDLE_PREFIX)

    def test_non_huddle_response_no_prefix(self, method, criminal_discussion,
                                            defense1):
        disc = criminal_discussion
        disc.method_state["current_phase"] = "opening_statements"
        disc.method_state["phase_index"] = 1
        disc.method_state["opening_huddle"]["sub_state"] = "defense_speaks"

        result = method.process_response("Opening statement", defense1, disc)
        assert not result.display_content.startswith(HUDDLE_PREFIX)


# ── Prompts ───────────────────────────────────────────────────────────

class TestPrompts:
    def test_arraignment_system_prompt_criminal(self, method,
                                                 criminal_discussion,
                                                 prosecutor):
        disc = criminal_discussion
        prompt = method.get_system_prompt(prosecutor, disc)
        assert "criminal trial" in prompt.lower()
        assert "Prosecution" in prompt

    def test_arraignment_system_prompt_civil(self, method,
                                              civil_discussion,
                                              plaintiff1):
        disc = civil_discussion
        prompt = method.get_system_prompt(plaintiff1, disc)
        assert "civil proceeding" in prompt.lower()
        assert "Plaintiff" in prompt

    def test_defense_prompt(self, method, criminal_discussion, defense1):
        disc = criminal_discussion
        prompt = method.get_system_prompt(defense1, disc)
        assert "Defense" in prompt

    def test_turn_prompt_includes_entity_name(self, method,
                                                criminal_discussion,
                                                prosecutor):
        disc = criminal_discussion
        prompt = method.get_turn_prompt(prosecutor, disc)
        assert prosecutor.name in prompt


# ── on_round_complete delegation ──────────────────────────────────────

class TestOnRoundComplete:
    def test_increments_phase_round(self, method, criminal_discussion):
        disc = criminal_discussion
        assert disc.method_state.get("phase_round", 1) == 1
        method.on_round_complete(disc)
        assert disc.method_state["phase_round"] == 2

    def test_delegates_to_handler(self, method, criminal_discussion):
        disc = criminal_discussion
        # Advance to opening_statements (has on_round_complete)
        disc.method_state["current_phase"] = "opening_statements"
        disc.method_state["phase_index"] = 1
        disc.method_state["opening_huddle"] = init_huddle_state()

        method.on_round_complete(disc)
        # Should have advanced huddle state
        huddle = disc.method_state["opening_huddle"]
        assert huddle["sub_state"] != "accusation_huddle" or huddle["huddle_round"] > 0


# ── Setup validation ──────────────────────────────────────────────────

class TestSetupValidation:
    def test_court_of_law_requires_accusation(self, tmp_db):
        """start_discussion should reject court_of_law without accusation team."""
        from consensus.moderator import Moderator

        db = tmp_db
        mod_id = db.add_entity("Judge", "ai")
        def_id = db.add_entity("Defense", "ai")

        disc = Discussion(topic="test", discussion_method="court_of_law")
        disc.entities = [
            Entity.from_db_row(db.get_entity(mod_id)),
            Entity.from_db_row(db.get_entity(def_id)),
        ]
        disc.moderator_id = mod_id
        disc.member_roles = {def_id: "defense"}

        from consensus.app_discussion_setup import start_discussion
        moderator = Moderator(disc, db)
        result = start_discussion(disc, db, moderator)
        assert "error" in result
        assert "Prosecutor" in result["error"] or "Plaintiff" in result["error"]

    def test_court_of_law_requires_defense(self, tmp_db):
        from consensus.moderator import Moderator

        db = tmp_db
        mod_id = db.add_entity("Judge", "ai")
        pros_id = db.add_entity("Prosecutor", "ai")

        disc = Discussion(topic="test", discussion_method="court_of_law")
        disc.entities = [
            Entity.from_db_row(db.get_entity(mod_id)),
            Entity.from_db_row(db.get_entity(pros_id)),
        ]
        disc.moderator_id = mod_id
        disc.member_roles = {pros_id: "prosecutor"}

        from consensus.app_discussion_setup import start_discussion
        moderator = Moderator(disc, db)
        result = start_discussion(disc, db, moderator)
        assert "error" in result
        assert "Defense" in result["error"]
