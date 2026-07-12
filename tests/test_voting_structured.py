"""Structured-output conversion of the voting phase (#23)."""

from consensus.methods.phases.vote import VoteHandler
from consensus.models import Discussion, Entity, EntityType


def _entity(eid: int = 7) -> Entity:
    return Entity(id=eid, name="Alice", entity_type=EntityType.AI)


def _discussion() -> Discussion:
    disc = Discussion(topic="t", discussion_method="voting")
    disc.method_state = {
        "current_phase": "vote", "phase_round": 1,
        "motions": [{"id": 1, "text": "Motion one"},
                    {"id": 2, "text": "Motion two"}],
        "votes": [],
    }
    return disc


PAYLOAD = {"votes": [
    {"motion_id": 1, "vote": "for", "rationale": "Good idea."},
    {"motion_id": 2, "vote": "against", "rationale": "Too risky."},
]}


class TestVoteValidation:
    def test_valid(self):
        assert VoteHandler().validate_output(
            PAYLOAD, _entity(), _discussion()) == ""

    def test_empty_votes_rejected(self):
        err = VoteHandler().validate_output(
            {"votes": []}, _entity(), _discussion())
        assert "votes" in err

    def test_unknown_motion_rejected(self):
        bad = {"votes": [{"motion_id": 99, "vote": "for",
                          "rationale": "x"}]}
        err = VoteHandler().validate_output(bad, _entity(), _discussion())
        assert "99" in err

    def test_invalid_vote_value_rejected(self):
        bad = {"votes": [{"motion_id": 1, "vote": "maybe",
                          "rationale": "x"}]}
        err = VoteHandler().validate_output(bad, _entity(), _discussion())
        assert "abstain" in err


class TestVoteProcessing:
    def test_structured_votes_recorded(self):
        handler = VoteHandler()
        disc = _discussion()
        processed = handler.process_structured_response(
            PAYLOAD, _entity(), disc)
        votes = disc.method_state["votes"]
        assert len(votes) == 2
        assert votes[0]["vote"] == "for"
        assert votes[0]["rationale"] == "Good idea."
        assert "**Votes cast:** 2" in processed.display_content

    def test_double_vote_deduplicated(self):
        handler = VoteHandler()
        disc = _discussion()
        handler.process_structured_response(PAYLOAD, _entity(), disc)
        handler.process_structured_response(PAYLOAD, _entity(), disc)
        assert len(disc.method_state["votes"]) == 2

    def test_free_text_path_still_records(self):
        handler = VoteHandler()
        disc = _discussion()
        content = ('```json\n{"vote": "for", "motion_id": 1}\n```\n'
                   "Because reasons.")
        handler.process_response(content, _entity(), disc)
        assert len(disc.method_state["votes"]) == 1

    def test_declares_output_tool(self):
        handler = VoteHandler()
        assert handler.requires_structured_output is True
        spec = handler.get_output_tool(_entity(), _discussion())
        assert spec.name == "submit_votes"
        assert "Motion one" in spec.description
