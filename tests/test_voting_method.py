"""Tests for the participant voting discussion method."""

import pytest

from consensus.methods.voting import VotingMethod
from consensus.methods.base import Phase
from consensus.models import Discussion, Entity, EntityType


def _make_discussion(n_participants=3, method_name="voting"):
    """Create a discussion with participants for testing."""
    entities = []
    mod = Entity(name="Moderator", entity_type=EntityType.AI, id=100)
    entities.append(mod)
    for i in range(n_participants):
        e = Entity(name=f"Participant_{i+1}", entity_type=EntityType.AI, id=i+1)
        entities.append(e)

    disc = Discussion(
        id=1,
        topic="Should we adopt policy X?",
        entities=entities,
        moderator_id=100,
        turn_order=[e.id for e in entities if e.id != 100],
        discussion_method=method_name,
    )
    return disc, mod


class TestVotingMethodInit:
    """Test method initialization and metadata."""

    def test_method_metadata(self):
        method = VotingMethod()
        assert method.name == "voting"
        assert method.display_name
        assert len(method.default_phases) >= 3

    def test_init_state(self):
        method = VotingMethod()
        disc, _ = _make_discussion()
        state = method.init_state(disc)
        assert "motions" in state
        assert "votes" in state
        assert state["current_phase"] == "deliberate"


class TestVotingMethodPrompts:
    """Test prompt generation for each phase."""

    def test_deliberate_system_prompt(self):
        method = VotingMethod()
        disc, _ = _make_discussion()
        disc.method_state = method.init_state(disc)
        entity = disc.entities[1]  # first participant

        prompt = method.get_system_prompt(entity, disc)
        assert "deliberat" in prompt.lower() or "discuss" in prompt.lower()

    def test_vote_system_prompt(self):
        method = VotingMethod()
        disc, _ = _make_discussion()
        disc.method_state = method.init_state(disc)
        disc.method_state["current_phase"] = "vote"
        disc.method_state["motions"] = [
            {"id": 1, "text": "Adopt policy X", "proposed_by": "Participant_1"},
        ]
        entity = disc.entities[1]

        prompt = method.get_system_prompt(entity, disc)
        assert "vote" in prompt.lower()
        assert "Adopt policy X" in prompt


class TestVotingResponseProcessing:
    """Test extraction of motions and votes from responses."""

    def test_extract_motion(self):
        method = VotingMethod()
        disc, _ = _make_discussion()
        disc.method_state = method.init_state(disc)
        entity = disc.entities[1]

        content = (
            'I think we should vote on this.\n\n'
            '```json\n'
            '{"motion": "Adopt policy X immediately"}\n'
            '```\n'
        )
        result = method.process_response(content, entity, disc)
        assert len(disc.method_state["motions"]) == 1
        assert disc.method_state["motions"][0]["text"] == "Adopt policy X immediately"

    def test_extract_vote(self):
        method = VotingMethod()
        disc, _ = _make_discussion()
        disc.method_state = method.init_state(disc)
        disc.method_state["current_phase"] = "vote"
        disc.method_state["motions"] = [
            {"id": 1, "text": "Adopt policy X", "proposed_by": "Participant_1"},
        ]
        entity = disc.entities[1]

        content = (
            'I support this motion.\n\n'
            '```json\n'
            '{"vote": "for", "motion_id": 1}\n'
            '```\n'
        )
        result = method.process_response(content, entity, disc)
        votes = disc.method_state["votes"]
        assert len(votes) == 1
        assert votes[0]["vote"] == "for"
        assert votes[0]["entity_id"] == entity.id

    def test_vote_values_validated(self):
        method = VotingMethod()
        disc, _ = _make_discussion()
        disc.method_state = method.init_state(disc)
        disc.method_state["current_phase"] = "vote"
        disc.method_state["motions"] = [
            {"id": 1, "text": "Test motion", "proposed_by": "Participant_1"},
        ]
        entity = disc.entities[1]

        # Invalid vote value
        content = '```json\n{"vote": "maybe", "motion_id": 1}\n```'
        result = method.process_response(content, entity, disc)
        assert len(disc.method_state["votes"]) == 0  # rejected


class TestVotingPhaseTransitions:
    """Test phase advancement logic."""

    def test_deliberate_advances_after_rounds(self):
        method = VotingMethod()
        disc, _ = _make_discussion()
        disc.method_state = method.init_state(disc)
        # Simulate completing the deliberation rounds
        disc.method_state["phase_round"] = 3  # past default rounds

        assert method.should_advance_phase(disc) is True

    def test_vote_advances_when_all_voted(self):
        method = VotingMethod()
        disc, _ = _make_discussion()
        disc.method_state = method.init_state(disc)
        disc.method_state["current_phase"] = "vote"
        disc.method_state["motions"] = [
            {"id": 1, "text": "Test", "proposed_by": "P1"},
        ]
        # All 3 participants vote
        for i in range(3):
            disc.method_state["votes"].append({
                "entity_id": i + 1,
                "motion_id": 1,
                "vote": "for",
            })

        assert method.should_advance_phase(disc) is True

    def test_tally_phase_produces_results(self):
        method = VotingMethod()
        disc, _ = _make_discussion()
        disc.method_state = method.init_state(disc)
        disc.method_state["current_phase"] = "vote"
        disc.method_state["motions"] = [
            {"id": 1, "text": "Test", "proposed_by": "P1"},
        ]
        disc.method_state["votes"] = [
            {"entity_id": 1, "motion_id": 1, "vote": "for"},
            {"entity_id": 2, "motion_id": 1, "vote": "for"},
            {"entity_id": 3, "motion_id": 1, "vote": "against"},
        ]

        tally = method._tally_votes(disc)
        assert tally[1]["for"] == 2
        assert tally[1]["against"] == 1

    def test_conclusion_prompt_includes_results(self):
        method = VotingMethod()
        disc, _ = _make_discussion()
        disc.method_state = method.init_state(disc)
        disc.method_state["current_phase"] = "tally"
        disc.method_state["motions"] = [
            {"id": 1, "text": "Adopt policy X", "proposed_by": "P1"},
        ]
        disc.method_state["votes"] = [
            {"entity_id": 1, "motion_id": 1, "vote": "for"},
            {"entity_id": 2, "motion_id": 1, "vote": "for"},
            {"entity_id": 3, "motion_id": 1, "vote": "against"},
        ]

        prompt = method.get_conclusion_prompt(disc)
        assert "Adopt policy X" in prompt
