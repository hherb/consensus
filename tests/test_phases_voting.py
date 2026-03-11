"""Tests for the composable Voting Method phase handlers and helpers."""

import pytest

from consensus.methods.base import Phase, ProcessedResponse
from consensus.methods.phases._voting_helpers import (
    DEFAULT_DELIBERATION_ROUNDS,
    VALID_VOTES,
    extract_motions,
    extract_votes,
    format_motions,
    format_motions_for_voting,
    format_tally,
    tally_votes,
)
from consensus.methods.phases.deliberate import DeliberateHandler
from consensus.methods.phases.tally import TallyHandler
from consensus.methods.phases.vote import VoteHandler
from consensus.methods.voting import VotingMethod
from consensus.models import Discussion, Entity, EntityType


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------

def _make_discussion(n_participants=3):
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
        discussion_method="voting",
    )
    return disc


def _init_discussion():
    """Create a discussion with initialized voting state."""
    disc = _make_discussion()
    method = VotingMethod()
    disc.method_state = method.init_state(disc)
    return disc


# ==================================================================
# Helper function tests
# ==================================================================

class TestExtractMotions:
    """Tests for extract_motions helper."""

    def test_single_motion(self):
        content = (
            'I propose:\n\n'
            '```json\n'
            '{"motion": "Adopt policy X immediately"}\n'
            '```\n'
        )
        result = extract_motions(content)
        assert result == ["Adopt policy X immediately"]

    def test_multiple_motions(self):
        content = (
            '```json\n{"motion": "First motion"}\n```\n\n'
            'Some text\n\n'
            '```json\n{"motion": "Second motion"}\n```\n'
        )
        result = extract_motions(content)
        assert len(result) == 2
        assert result[0] == "First motion"
        assert result[1] == "Second motion"

    def test_no_motion_key(self):
        content = '```json\n{"vote": "for", "motion_id": 1}\n```'
        result = extract_motions(content)
        assert result == []

    def test_invalid_json(self):
        content = '```json\n{not valid json}\n```'
        result = extract_motions(content)
        assert result == []

    def test_empty_content(self):
        assert extract_motions("") == []

    def test_motion_whitespace_stripped(self):
        content = '```json\n{"motion": "  Trim me  "}\n```'
        result = extract_motions(content)
        assert result == ["Trim me"]


class TestExtractVotes:
    """Tests for extract_votes helper."""

    def test_single_vote(self):
        content = '```json\n{"vote": "for", "motion_id": 1}\n```'
        result = extract_votes(content)
        assert len(result) == 1
        assert result[0]["vote"] == "for"
        assert result[0]["motion_id"] == 1

    def test_multiple_votes(self):
        content = (
            '```json\n{"vote": "for", "motion_id": 1}\n```\n'
            '```json\n{"vote": "against", "motion_id": 2}\n```\n'
        )
        result = extract_votes(content)
        assert len(result) == 2

    def test_missing_vote_key(self):
        content = '```json\n{"motion_id": 1}\n```'
        result = extract_votes(content)
        assert result == []

    def test_missing_motion_id_key(self):
        content = '```json\n{"vote": "for"}\n```'
        result = extract_votes(content)
        assert result == []


class TestTallyVotes:
    """Tests for tally_votes helper."""

    def test_basic_tally(self):
        disc = _init_discussion()
        disc.method_state["motions"] = [
            {"id": 1, "text": "Test", "proposed_by": "P1"},
        ]
        disc.method_state["votes"] = [
            {"entity_id": 1, "motion_id": 1, "vote": "for"},
            {"entity_id": 2, "motion_id": 1, "vote": "for"},
            {"entity_id": 3, "motion_id": 1, "vote": "against"},
        ]
        result = tally_votes(disc)
        assert result[1]["for"] == 2
        assert result[1]["against"] == 1
        assert result[1]["abstain"] == 0

    def test_empty_votes(self):
        disc = _init_discussion()
        disc.method_state["motions"] = [
            {"id": 1, "text": "Test", "proposed_by": "P1"},
        ]
        result = tally_votes(disc)
        assert result[1] == {"for": 0, "against": 0, "abstain": 0}

    def test_multiple_motions(self):
        disc = _init_discussion()
        disc.method_state["motions"] = [
            {"id": 1, "text": "A", "proposed_by": "P1"},
            {"id": 2, "text": "B", "proposed_by": "P2"},
        ]
        disc.method_state["votes"] = [
            {"entity_id": 1, "motion_id": 1, "vote": "for"},
            {"entity_id": 1, "motion_id": 2, "vote": "against"},
        ]
        result = tally_votes(disc)
        assert result[1]["for"] == 1
        assert result[2]["against"] == 1


class TestFormatMotions:
    """Tests for format_motions helper."""

    def test_no_motions(self):
        result = format_motions({"motions": []})
        assert "No motions" in result

    def test_with_motions(self):
        state = {"motions": [
            {"id": 1, "text": "Do X", "proposed_by": "Alice"},
            {"id": 2, "text": "Do Y", "proposed_by": "Bob"},
        ]}
        result = format_motions(state)
        assert "Motion 1" in result
        assert "Do X" in result
        assert "Alice" in result
        assert "Motion 2" in result


class TestFormatMotionsForVoting:
    """Tests for format_motions_for_voting helper."""

    def test_no_motions(self):
        result = format_motions_for_voting({"motions": []})
        assert "No motions" in result

    def test_with_motions(self):
        state = {"motions": [
            {"id": 1, "text": "Do X", "proposed_by": "Alice"},
        ]}
        result = format_motions_for_voting(state)
        assert "Motion 1" in result
        assert "Do X" in result
        # Should NOT include proposed_by in voting format
        assert "Alice" not in result


class TestFormatTally:
    """Tests for format_tally helper."""

    def test_simple_majority_pass(self):
        disc = _init_discussion()
        disc.method_state["threshold"] = "simple_majority"
        motions = [{"id": 1, "text": "Test motion"}]
        tally = {1: {"for": 2, "against": 1, "abstain": 0}}
        result = format_tally(tally, motions, disc)
        assert "PASSED" in result

    def test_simple_majority_fail(self):
        disc = _init_discussion()
        disc.method_state["threshold"] = "simple_majority"
        motions = [{"id": 1, "text": "Test motion"}]
        tally = {1: {"for": 1, "against": 2, "abstain": 0}}
        result = format_tally(tally, motions, disc)
        assert "FAILED" in result

    def test_unanimous_pass(self):
        disc = _init_discussion()
        disc.method_state["threshold"] = "unanimous"
        motions = [{"id": 1, "text": "Test motion"}]
        tally = {1: {"for": 3, "against": 0, "abstain": 0}}
        result = format_tally(tally, motions, disc)
        assert "PASSED" in result

    def test_unanimous_fail(self):
        disc = _init_discussion()
        disc.method_state["threshold"] = "unanimous"
        motions = [{"id": 1, "text": "Test motion"}]
        tally = {1: {"for": 2, "against": 1, "abstain": 0}}
        result = format_tally(tally, motions, disc)
        assert "FAILED" in result

    def test_supermajority(self):
        disc = _init_discussion()
        disc.method_state["threshold"] = "supermajority"
        motions = [{"id": 1, "text": "Test motion"}]
        # 2/3 = 0.667, need >= 2/3
        tally = {1: {"for": 2, "against": 1, "abstain": 0}}
        result = format_tally(tally, motions, disc)
        assert "PASSED" in result


class TestConstants:
    """Tests for module constants."""

    def test_valid_votes(self):
        assert VALID_VOTES == {"for", "against", "abstain"}

    def test_default_deliberation_rounds(self):
        assert DEFAULT_DELIBERATION_ROUNDS == 2


# ==================================================================
# DeliberateHandler tests
# ==================================================================

class TestDeliberateHandler:
    """Tests for the deliberation phase handler."""

    def setup_method(self):
        self.handler = DeliberateHandler()

    def test_phase_metadata(self):
        assert self.handler.phase.name == "deliberate"
        assert self.handler.phase.display_name == "Deliberation"
        assert self.handler.phase.rounds == DEFAULT_DELIBERATION_ROUNDS

    def test_init_state(self):
        disc = _make_discussion()
        state = self.handler.init_state(disc)
        assert state["motions"] == []
        assert state["votes"] == []
        assert state["next_motion_id"] == 1
        assert state["threshold"] == "simple_majority"

    def test_system_prompt_includes_json_format(self):
        disc = _init_discussion()
        entity = disc.entities[1]
        prompt = self.handler.get_system_prompt(entity, disc)
        assert "json" in prompt.lower()
        assert "motion" in prompt.lower()
        assert entity.name in prompt
        assert disc.topic in prompt

    def test_system_prompt_shows_existing_motions(self):
        disc = _init_discussion()
        disc.method_state["motions"] = [
            {"id": 1, "text": "Test motion", "proposed_by": "Alice"},
        ]
        entity = disc.entities[1]
        prompt = self.handler.get_system_prompt(entity, disc)
        assert "Test motion" in prompt

    def test_turn_prompt(self):
        disc = _init_discussion()
        entity = disc.entities[1]
        prompt = self.handler.get_turn_prompt(entity, disc)
        assert entity.name in prompt

    def test_summary_prompt(self):
        disc = _init_discussion()
        disc.method_state["motions"] = [
            {"id": 1, "text": "M1", "proposed_by": "P1"},
        ]
        prompt = self.handler.get_summary_prompt(disc, "Alice", "Bob")
        assert "Alice" in prompt
        assert "Bob" in prompt
        assert "1 motion" in prompt

    def test_process_response_extracts_motion(self):
        disc = _init_discussion()
        entity = disc.entities[1]
        content = (
            'I propose:\n\n'
            '```json\n'
            '{"motion": "Adopt policy X"}\n'
            '```\n'
        )
        result = self.handler.process_response(content, entity, disc)
        assert len(disc.method_state["motions"]) == 1
        assert disc.method_state["motions"][0]["text"] == "Adopt policy X"
        assert disc.method_state["motions"][0]["proposed_by"] == entity.name
        assert disc.method_state["motions"][0]["id"] == 1
        assert disc.method_state["next_motion_id"] == 2
        assert "Motions proposed" in result.display_content

    def test_process_response_no_motion(self):
        disc = _init_discussion()
        entity = disc.entities[1]
        content = "Just sharing my thoughts on the topic."
        result = self.handler.process_response(content, entity, disc)
        assert len(disc.method_state["motions"]) == 0
        assert result.display_content == content

    def test_should_advance_after_rounds(self):
        disc = _init_discussion()
        disc.method_state["phase_round"] = DEFAULT_DELIBERATION_ROUNDS + 1
        assert self.handler.should_advance(disc) is True

    def test_should_not_advance_during_rounds(self):
        disc = _init_discussion()
        disc.method_state["phase_round"] = 1
        assert self.handler.should_advance(disc) is False


# ==================================================================
# VoteHandler tests
# ==================================================================

class TestVoteHandler:
    """Tests for the voting phase handler."""

    def setup_method(self):
        self.handler = VoteHandler()

    def test_phase_metadata(self):
        assert self.handler.phase.name == "vote"
        assert self.handler.phase.display_name == "Voting"
        assert self.handler.phase.rounds == 1

    def test_system_prompt_includes_motions(self):
        disc = _init_discussion()
        disc.method_state["current_phase"] = "vote"
        disc.method_state["motions"] = [
            {"id": 1, "text": "Adopt policy X", "proposed_by": "P1"},
        ]
        entity = disc.entities[1]
        prompt = self.handler.get_system_prompt(entity, disc)
        assert "vote" in prompt.lower()
        assert "Adopt policy X" in prompt

    def test_turn_prompt_lists_unvoted_motions(self):
        disc = _init_discussion()
        disc.method_state["current_phase"] = "vote"
        disc.method_state["motions"] = [
            {"id": 1, "text": "Motion A", "proposed_by": "P1"},
            {"id": 2, "text": "Motion B", "proposed_by": "P2"},
        ]
        entity = disc.entities[1]
        # Entity has voted on motion 1 but not motion 2
        disc.method_state["votes"] = [
            {"entity_id": entity.id, "motion_id": 1, "vote": "for"},
        ]
        prompt = self.handler.get_turn_prompt(entity, disc)
        assert "Motion B" in prompt
        # Motion A should not be in unvoted list
        assert "Motion A" not in prompt or "already voted" in prompt.lower()

    def test_turn_prompt_all_voted(self):
        disc = _init_discussion()
        disc.method_state["current_phase"] = "vote"
        disc.method_state["motions"] = [
            {"id": 1, "text": "Motion A", "proposed_by": "P1"},
        ]
        entity = disc.entities[1]
        disc.method_state["votes"] = [
            {"entity_id": entity.id, "motion_id": 1, "vote": "for"},
        ]
        prompt = self.handler.get_turn_prompt(entity, disc)
        assert "already voted" in prompt.lower()

    def test_summary_prompt(self):
        disc = _init_discussion()
        prompt = self.handler.get_summary_prompt(disc, "Alice", "Bob")
        assert "Alice" in prompt
        assert "Bob" in prompt
        assert "vote" in prompt.lower()

    def test_process_response_valid_vote(self):
        disc = _init_discussion()
        disc.method_state["current_phase"] = "vote"
        disc.method_state["motions"] = [
            {"id": 1, "text": "Test", "proposed_by": "P1"},
        ]
        entity = disc.entities[1]
        content = '```json\n{"vote": "for", "motion_id": 1}\n```\nI agree.'
        result = self.handler.process_response(content, entity, disc)
        assert len(disc.method_state["votes"]) == 1
        assert disc.method_state["votes"][0]["vote"] == "for"
        assert disc.method_state["votes"][0]["entity_id"] == entity.id
        assert result.extracted_data["votes_cast"] == 1

    def test_process_response_invalid_vote_value(self):
        disc = _init_discussion()
        disc.method_state["current_phase"] = "vote"
        disc.method_state["motions"] = [
            {"id": 1, "text": "Test", "proposed_by": "P1"},
        ]
        entity = disc.entities[1]
        content = '```json\n{"vote": "maybe", "motion_id": 1}\n```'
        result = self.handler.process_response(content, entity, disc)
        assert len(disc.method_state["votes"]) == 0
        assert result.extracted_data["votes_cast"] == 0

    def test_process_response_invalid_motion_id(self):
        disc = _init_discussion()
        disc.method_state["current_phase"] = "vote"
        disc.method_state["motions"] = [
            {"id": 1, "text": "Test", "proposed_by": "P1"},
        ]
        entity = disc.entities[1]
        content = '```json\n{"vote": "for", "motion_id": 99}\n```'
        result = self.handler.process_response(content, entity, disc)
        assert len(disc.method_state["votes"]) == 0

    def test_process_response_no_double_vote(self):
        disc = _init_discussion()
        disc.method_state["current_phase"] = "vote"
        disc.method_state["motions"] = [
            {"id": 1, "text": "Test", "proposed_by": "P1"},
        ]
        entity = disc.entities[1]
        # First vote accepted
        disc.method_state["votes"] = [
            {"entity_id": entity.id, "motion_id": 1, "vote": "for"},
        ]
        content = '```json\n{"vote": "against", "motion_id": 1}\n```'
        result = self.handler.process_response(content, entity, disc)
        # Should still be just 1 vote
        assert len(disc.method_state["votes"]) == 1
        assert disc.method_state["votes"][0]["vote"] == "for"

    def test_should_advance_all_votes_in(self):
        disc = _init_discussion()
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
        assert self.handler.should_advance(disc) is True

    def test_should_not_advance_missing_votes(self):
        disc = _init_discussion()
        disc.method_state["current_phase"] = "vote"
        disc.method_state["motions"] = [
            {"id": 1, "text": "Test", "proposed_by": "P1"},
        ]
        # Only 1 of 3 participants voted
        disc.method_state["votes"].append({
            "entity_id": 1,
            "motion_id": 1,
            "vote": "for",
        })
        assert self.handler.should_advance(disc) is False

    def test_should_not_advance_no_motions(self):
        disc = _init_discussion()
        disc.method_state["current_phase"] = "vote"
        assert self.handler.should_advance(disc) is False

    def test_transition_message_with_motions(self):
        disc = _init_discussion()
        disc.method_state["motions"] = [
            {"id": 1, "text": "Do X", "proposed_by": "P1"},
        ]
        msg = self.handler.get_transition_message(disc)
        assert "Voting" in msg
        assert "Do X" in msg
        assert "1 motion" in msg

    def test_transition_message_no_motions(self):
        disc = _init_discussion()
        msg = self.handler.get_transition_message(disc)
        assert "No motions" in msg


# ==================================================================
# TallyHandler tests
# ==================================================================

class TestTallyHandler:
    """Tests for the tally phase handler."""

    def setup_method(self):
        self.handler = TallyHandler()

    def test_phase_metadata(self):
        assert self.handler.phase.name == "tally"
        assert self.handler.phase.display_name == "Results & Synthesis"
        assert self.handler.phase.rounds == 1

    def test_system_prompt_empty(self):
        disc = _init_discussion()
        entity = disc.entities[1]
        assert self.handler.get_system_prompt(entity, disc) == ""

    def test_turn_prompt_empty(self):
        disc = _init_discussion()
        entity = disc.entities[1]
        assert self.handler.get_turn_prompt(entity, disc) == ""

    def test_should_advance_after_round(self):
        disc = _init_discussion()
        disc.method_state["phase_round"] = 2
        assert self.handler.should_advance(disc) is True

    def test_should_not_advance_first_round(self):
        disc = _init_discussion()
        disc.method_state["phase_round"] = 1
        assert self.handler.should_advance(disc) is False

    def test_transition_message(self):
        disc = _init_discussion()
        msg = self.handler.get_transition_message(disc)
        assert "All votes are in" in msg
        assert "Results" in msg


# ==================================================================
# VotingMethod integration tests
# ==================================================================

class TestVotingMethodAssembly:
    """Tests for the refactored VotingMethod thin assembly."""

    def test_phase_handlers_set(self):
        method = VotingMethod()
        assert len(method.phase_handlers) == 3
        assert isinstance(method.phase_handlers[0], DeliberateHandler)
        assert isinstance(method.phase_handlers[1], VoteHandler)
        assert isinstance(method.phase_handlers[2], TallyHandler)

    def test_default_phases_auto_derived(self):
        method = VotingMethod()
        assert len(method.default_phases) == 3
        assert method.default_phases[0].name == "deliberate"
        assert method.default_phases[1].name == "vote"
        assert method.default_phases[2].name == "tally"

    def test_init_state_merges_handler_state(self):
        method = VotingMethod()
        disc = _make_discussion()
        state = method.init_state(disc)
        assert "motions" in state
        assert "votes" in state
        assert "next_motion_id" in state
        assert "threshold" in state
        assert state["current_phase"] == "deliberate"

    def test_conclusion_prompt_includes_tally(self):
        method = VotingMethod()
        disc = _init_discussion()
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
        assert "PASSED" in prompt

    def test_method_metadata(self):
        method = VotingMethod()
        assert method.name == "voting"
        assert method.display_name == "Participant Voting"
        assert len(method.description) > 0

    def test_delegated_system_prompt(self):
        """Verify the base class delegates to the handler."""
        method = VotingMethod()
        disc = _init_discussion()
        entity = disc.entities[1]
        prompt = method.get_system_prompt(entity, disc)
        assert "DELIBERATION" in prompt

    def test_delegated_should_advance(self):
        """Verify the base class delegates should_advance_phase."""
        method = VotingMethod()
        disc = _init_discussion()
        disc.method_state["phase_round"] = DEFAULT_DELIBERATION_ROUNDS + 1
        assert method.should_advance_phase(disc) is True

    def test_delegated_process_response(self):
        """Verify the base class delegates process_response."""
        method = VotingMethod()
        disc = _init_discussion()
        entity = disc.entities[1]
        content = '```json\n{"motion": "Test motion"}\n```'
        result = method.process_response(content, entity, disc)
        assert len(disc.method_state["motions"]) == 1

    def test_delegated_transition_message(self):
        """Verify the base class delegates get_phase_transition_message."""
        method = VotingMethod()
        disc = _init_discussion()
        disc.method_state["motions"] = [
            {"id": 1, "text": "Do X", "proposed_by": "P1"},
        ]
        vote_phase = method.default_phases[1]
        msg = method.get_phase_transition_message(vote_phase, disc)
        assert "Voting" in msg
        assert "Do X" in msg

    def test_to_dict(self):
        method = VotingMethod()
        d = method.to_dict()
        assert d["name"] == "voting"
        assert len(d["phases"]) == 3
