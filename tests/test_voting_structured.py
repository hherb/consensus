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

    def test_duplicate_motion_in_payload_rejected(self):
        bad = {"votes": [
            {"motion_id": 1, "vote": "for", "rationale": "x"},
            {"motion_id": 1, "vote": "against", "rationale": "y"},
        ]}
        err = VoteHandler().validate_output(bad, _entity(), _discussion())
        assert "1" in err

    def test_already_voted_motion_rejected(self):
        """A vote on a motion this entity already voted on is rejected
        at validation time, so the displayed vote lines always match
        what record_votes accepts."""
        disc = _discussion()
        disc.method_state["votes"].append(
            {"entity_id": 7, "entity_name": "Alice", "motion_id": 1,
             "vote": "for", "rationale": "earlier"})
        bad = {"votes": [{"motion_id": 1, "vote": "against",
                          "rationale": "changed my mind"}]}
        err = VoteHandler().validate_output(bad, _entity(), disc)
        assert "already voted" in err.lower()
        # The error steers the model to its pending motions
        assert "2" in err

    def test_other_entities_votes_do_not_block(self):
        disc = _discussion()
        disc.method_state["votes"].append(
            {"entity_id": 99, "entity_name": "Bob", "motion_id": 1,
             "vote": "for", "rationale": "x"})
        payload = {"votes": [{"motion_id": 1, "vote": "against",
                              "rationale": "y"}]}
        assert VoteHandler().validate_output(
            payload, _entity(), disc) == ""


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

    def test_null_rationale_becomes_empty_not_none_string(self):
        """A JSON ``null`` rationale must record as "" — never the literal
        "None" (the coerce_str bug class)."""
        handler = VoteHandler()
        disc = _discussion()
        payload = {"votes": [{"motion_id": 1, "vote": "for",
                              "rationale": None}]}
        processed = handler.process_structured_response(
            payload, _entity(), disc)
        assert disc.method_state["votes"][0]["rationale"] == ""
        assert "None" not in processed.display_content

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


class TestFullyVotedEntity:
    def test_no_output_tool_when_all_motions_voted(self):
        """An entity that already voted on every motion must not be
        forced through submit_votes — no payload could validate, so the
        turn would burn all retries and end in a warning.  The free-text
        path handles the 'already voted on all motions' prose turn."""
        handler = VoteHandler()
        disc = _discussion()
        disc.method_state["votes"] = [
            {"entity_id": 7, "entity_name": "Alice", "motion_id": 1,
             "vote": "for", "rationale": "x"},
            {"entity_id": 7, "entity_name": "Alice", "motion_id": 2,
             "vote": "against", "rationale": "y"},
        ]
        assert handler.get_output_tool(_entity(), disc) is None

    def test_output_tool_present_with_pending_motions(self):
        handler = VoteHandler()
        disc = _discussion()
        disc.method_state["votes"] = [
            {"entity_id": 7, "entity_name": "Alice", "motion_id": 1,
             "vote": "for", "rationale": "x"},
        ]
        spec = handler.get_output_tool(_entity(), disc)
        assert spec is not None and spec.name == "submit_votes"


class TestRecordVotesGuards:
    def test_explicit_none_vote_skipped_without_crash(self):
        """A JSON-extracted entry with vote: null must be skipped, not
        crash on None.lower()."""
        from consensus.methods.phases._voting_helpers import record_votes
        disc = _discussion()
        accepted = record_votes(
            disc.method_state, _entity(),
            [{"motion_id": 1, "vote": None, "rationale": ""}])
        assert accepted == 0
        assert disc.method_state["votes"] == []

    def test_non_string_vote_skipped_without_crash(self):
        """A JSON-extracted entry with a non-string vote (e.g. 1) must be
        skipped, not crash on int.lower()."""
        from consensus.methods.phases._voting_helpers import record_votes
        disc = _discussion()
        accepted = record_votes(
            disc.method_state, _entity(),
            [{"motion_id": 1, "vote": 1, "rationale": ""}])
        assert accepted == 0
        assert disc.method_state["votes"] == []
