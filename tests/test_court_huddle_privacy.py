"""Regression tests for Court of Law huddle privacy (GitHub issue #12).

The moderator prepends a ``[Name]: `` speaker prefix to context messages
BEFORE the method's ``filter_context_message`` hook runs, so huddle
suppression must tolerate that prefix.  Also covers the huddle round
count (2 rounds, not 3) and dropping suppressed messages from the API
payload entirely.
"""

import pytest

from consensus.methods.phases._court_helpers import (
    HUDDLE_PREFIX,
    advance_huddle_state,
    filter_huddle_message,
    init_huddle_state,
)
from consensus.moderator import Moderator
from consensus.models import (
    Discussion, Entity, EntityType, Message, MessageRole,
)


def _entity(db, name: str) -> Entity:
    eid = db.add_entity(name, "human", "#123456")
    return Entity.from_db_row(db.get_entity(eid))


@pytest.fixture
def court_discussion(tmp_db):
    """A criminal trial: judge (moderator), 2 prosecutors, 1 defense."""
    judge = _entity(tmp_db, "Judge")
    pros1 = _entity(tmp_db, "Alice")
    pros2 = _entity(tmp_db, "Carol")
    defense = _entity(tmp_db, "Bob")
    disc = Discussion(
        topic="The People v. Test",
        entities=[judge, pros1, pros2, defense],
        moderator_id=judge.id,
        turn_order=[pros1.id, pros2.id, defense.id],
        base_turn_order=[pros1.id, pros2.id, defense.id],
        member_roles={
            pros1.id: "prosecutor",
            pros2.id: "prosecutor",
            defense.id: "defense",
        },
        discussion_method="court_of_law",
        method_state={"trial_type": "criminal"},
        is_active=True,
        status="active",
    )
    return disc, judge, pros1, pros2, defense


class TestFilterHuddleMessage:
    """Huddle suppression must survive the '[Name]: ' speaker prefix."""

    def test_opposing_team_reader_is_suppressed(self, court_discussion):
        disc, judge, pros1, pros2, defense = court_discussion
        content = f"[Alice]: {HUDDLE_PREFIX}Our secret strategy is X."
        result = filter_huddle_message(
            "Alice", content, disc, current_entity_id=defense.id)
        assert result == ""

    def test_judge_reader_is_suppressed(self, court_discussion):
        disc, judge, pros1, pros2, defense = court_discussion
        content = f"[Alice]: {HUDDLE_PREFIX}Our secret strategy is X."
        result = filter_huddle_message(
            "Alice", content, disc, current_entity_id=judge.id)
        assert result == ""

    def test_same_team_reader_keeps_content(self, court_discussion):
        disc, judge, pros1, pros2, defense = court_discussion
        content = f"[Alice]: {HUDDLE_PREFIX}Our secret strategy is X."
        result = filter_huddle_message(
            "Alice", content, disc, current_entity_id=pros2.id)
        assert result == content

    def test_unprefixed_own_message_kept(self, court_discussion):
        """The author's own (assistant-role, unprefixed) message is kept."""
        disc, judge, pros1, pros2, defense = court_discussion
        content = f"{HUDDLE_PREFIX}Our secret strategy is X."
        result = filter_huddle_message(
            "Alice", content, disc, current_entity_id=pros1.id)
        assert result == content

    def test_non_huddle_message_untouched(self, court_discussion):
        disc, judge, pros1, pros2, defense = court_discussion
        content = "[Alice]: The evidence clearly shows the defendant's guilt."
        result = filter_huddle_message(
            "Alice", content, disc, current_entity_id=defense.id)
        assert result == content


class TestFormatMessagesHuddlePrivacy:
    """End-to-end through Moderator._format_messages (the real pipeline)."""

    def test_opposing_huddle_absent_from_payload(
        self, tmp_db, court_discussion,
    ):
        disc, judge, pros1, pros2, defense = court_discussion
        huddle_msg = Message(
            entity_id=pros1.id, entity_name=pros1.name,
            content=f"{HUDDLE_PREFIX}Push the fingerprint angle.",
            role=MessageRole.PARTICIPANT,
        )
        moderator = Moderator(disc, tmp_db)

        payload = moderator._format_messages(
            [huddle_msg], "system prompt", "your turn",
            current_entity_id=defense.id,
        )

        joined = str(payload)
        assert "fingerprint" not in joined, (
            "defense must not see the prosecution huddle"
        )
        assert all(
            m["content"] for m in payload
        ), "suppressed messages must be dropped, not sent as empty content"

    def test_same_team_sees_huddle(self, tmp_db, court_discussion):
        disc, judge, pros1, pros2, defense = court_discussion
        huddle_msg = Message(
            entity_id=pros1.id, entity_name=pros1.name,
            content=f"{HUDDLE_PREFIX}Push the fingerprint angle.",
            role=MessageRole.PARTICIPANT,
        )
        moderator = Moderator(disc, tmp_db)

        payload = moderator._format_messages(
            [huddle_msg], "system prompt", "your turn",
            current_entity_id=pros2.id,
        )

        assert "fingerprint" in str(payload)


class TestHuddleRoundCount:
    """Huddles run at most 2 rounds (docstring: 'round 1-2')."""

    def test_two_round_huddle_then_spokesperson(self, court_discussion):
        disc, judge, pros1, pros2, defense = court_discussion
        disc.method_state["huddle"] = init_huddle_state()

        # Round 1 completes
        advance_huddle_state(disc)
        assert disc.method_state["huddle"]["sub_state"] == "accusation_huddle"
        # Round 2 completes — huddle is over, spokesperson speaks next
        advance_huddle_state(disc)
        assert disc.method_state["huddle"]["sub_state"] == "accusation_speaks"
