"""Tests for the post-start speaking rotation (issue #104).

Mid-discussion membership and role changes used to update
``discussion.turn_order`` in memory only, so a removed member came back at
the next phase transition (``base_turn_order`` still held them), a late
joiner was skipped, and a reload restored the devil's advocate's old slot
(``turn_position`` was never rewritten).  These drive the real setup,
``complete_turn`` and ``load_discussion`` paths.
"""

import pytest

from consensus.app_discussion_flow import complete_turn
from consensus.app_discussion_roles import set_participant_role
from consensus.app_discussion_rotation import (
    carry_turn_index,
    devils_advocate_last,
)
from consensus.app_discussion_setup import (
    add_to_discussion,
    remove_from_discussion,
    start_discussion,
)
from consensus.app_discussion_state import load_discussion
from consensus.moderator import Moderator
from consensus.models import Discussion
from consensus.pricing import PricingCache

DA = "devils_advocate"


def _start(db, method: str = "open_discussion", n: int = 2):
    """Start a discussion: human moderator plus ``n`` human participants.

    Returns ``(discussion, moderator_id, participant_ids)``.
    """
    mod = db.add_entity("Mod", "human", "#000000")
    parts = [db.add_entity(f"P{i + 1}", "human", "#111111")
             for i in range(n)]
    disc = Discussion(topic="Rotation topic")
    add_to_discussion(disc, db, mod, is_moderator=True)
    for eid in parts:
        add_to_discussion(disc, db, eid)
    disc.discussion_method = method
    result = start_discussion(disc, db, Moderator(disc, db))
    assert "error" not in result, result
    return disc, mod, parts


def _reload(db, disc: Discussion) -> Discussion:
    loaded = load_discussion(
        db, disc.id, key_resolver=lambda pid, env: "", tool_registry=None)
    assert not isinstance(loaded, dict), f"load failed: {loaded}"
    return loaded[0]


async def _complete_turn(disc: Discussion, db) -> dict:
    """Finish the current turn through the human-moderator summary path."""
    return await complete_turn(
        disc, Moderator(disc, db), db, PricingCache(db.conn, db._lock),
        get_state_fn=lambda: {}, moderator_summary="Summary.")


class TestPureHelpers:
    def test_devils_advocate_moved_last(self):
        roles = {1: "standard", 2: DA, 3: "standard"}
        assert devils_advocate_last([1, 2, 3], roles) == [1, 3, 2]

    def test_no_devils_advocate_keeps_order(self):
        assert devils_advocate_last([3, 1, 2], {}) == [3, 1, 2]

    def test_turn_stays_with_its_holder(self):
        # [A, B, DA] with DA's turn; C joins before DA -> still DA's turn.
        assert carry_turn_index([1, 2, 9], [1, 2, 3, 9], 2) == 3

    def test_departed_holder_passes_turn_to_next(self):
        assert carry_turn_index([1, 2, 3], [1, 3], 1) == 1

    def test_departed_last_holder_wraps(self):
        assert carry_turn_index([1, 2, 3], [1, 2], 2) == 0

    def test_empty_orders(self):
        assert carry_turn_index([], [1], 0) == 0
        assert carry_turn_index([1], [], 0) == 0


class TestMembershipChanges:
    def test_issue_repro_roster_follows_changes(self, tmp_db):
        """The reproduction from #104: remove B, add C."""
        disc, _mod, (a, b) = _start(tmp_db)
        c = tmp_db.add_entity("C", "human", "#222222")

        remove_from_discussion(disc, tmp_db, b)
        add_to_discussion(disc, tmp_db, c)

        assert disc.turn_order == [a, c]
        assert disc.base_turn_order == [a, c]
        restored = _reload(tmp_db, disc)
        assert restored.turn_order == [a, c]
        assert restored.base_turn_order == [a, c]

    def test_recorded_phase_order_includes_joiner(self, tmp_db):
        """``_turn_order`` (what a reload restores) must list the joiner."""
        disc, _mod, (a, b) = _start(tmp_db)
        disc.method_state["_turn_order"] = [a, b]  # as a transition records
        c = tmp_db.add_entity("C", "human", "#222222")

        add_to_discussion(disc, tmp_db, c)

        assert _reload(tmp_db, disc).turn_order == [a, b, c]

    def test_joiner_lands_before_devils_advocate(self, tmp_db):
        disc, _mod, (a, b) = _start(tmp_db)
        set_participant_role(disc, tmp_db, a, DA)
        c = tmp_db.add_entity("C", "human", "#222222")

        add_to_discussion(disc, tmp_db, c)

        assert disc.turn_order == [b, c, a]
        assert _reload(tmp_db, disc).turn_order == [b, c, a]

    def test_join_keeps_whose_turn_it_is(self, tmp_db):
        disc, _mod, (a, b) = _start(tmp_db)
        set_participant_role(disc, tmp_db, a, DA)  # [b, a]
        disc.current_turn_index = 1  # the DA's turn
        c = tmp_db.add_entity("C", "human", "#222222")

        add_to_discussion(disc, tmp_db, c)

        assert disc.current_speaker.id == a

    def test_joiner_waits_out_a_narrowed_phase(self, tmp_db):
        """A moderator-only phase is not widened by a late joiner."""
        disc, mod, parts = _start(tmp_db, "counterfactual")
        disc.method_state["current_phase"] = "extract"
        disc.method_state["claims"] = [{"id": 1, "text": "A claim"}]
        disc.turn_order = [mod]
        disc.method_state["_turn_order"] = [mod]
        disc.current_turn_index = 0
        c = tmp_db.add_entity("C", "human", "#222222")

        add_to_discussion(disc, tmp_db, c)
        assert disc.turn_order == [mod]
        restored = _reload(tmp_db, disc)
        assert restored.turn_order == [mod]
        # The joiner is on the roster, so the next phase seats them.
        assert restored.base_turn_order == [*parts, c]

    def test_leaver_drops_out_of_a_narrowed_phase(self, tmp_db):
        """The recorded phase order must lose the leaver, or a reload
        discards it and widens the phase to the whole roster."""
        disc, _mod, (a, b, c) = _start(tmp_db, n=3)
        disc.turn_order = [a, b]
        disc.method_state["_turn_order"] = [a, b]
        disc.current_turn_index = 0

        remove_from_discussion(disc, tmp_db, b)

        restored = _reload(tmp_db, disc)
        assert restored.turn_order == [a]
        assert restored.base_turn_order == [a, c]

    def test_emptied_phase_falls_back_to_roster(self, tmp_db):
        """Removing a narrowed phase's only speaker (while paused) must not
        leave the discussion without one."""
        disc, _mod, (a, b) = _start(tmp_db)
        disc.status = "paused"
        disc.turn_order = [a]
        disc.method_state["_turn_order"] = [a]
        disc.current_turn_index = 0

        assert remove_from_discussion(disc, tmp_db, a) is True

        assert disc.turn_order == [b]
        assert disc.current_speaker.id == b
        assert disc.method_state["_turn_order"] == [b]
        assert _reload(tmp_db, disc).turn_order == [b]

    def test_removing_a_non_member_is_an_error(self, tmp_db):
        disc, _mod, (a, b) = _start(tmp_db)
        stranger = tmp_db.add_entity("S", "human", "#333333")
        n_messages = len(disc.messages)

        assert "error" in remove_from_discussion(disc, tmp_db, stranger)
        assert len(disc.messages) == n_messages
        assert disc.turn_order == [a, b]

    def test_removal_keeps_fresh_stamp_on_its_member(self, tmp_db):
        """A fresh ``_turn_index`` stamp (issue #19) survives a removal."""
        disc, _mod, (a, b, c) = _start(tmp_db, n=3)
        disc.current_turn_index = 1  # b's turn
        disc.method_state["_turn_index"] = 1
        disc.method_state["_turn_index_turn"] = disc.turn_number

        remove_from_discussion(disc, tmp_db, a)

        assert disc.current_speaker.id == b
        assert _reload(tmp_db, disc).current_speaker.id == b

    @pytest.mark.asyncio
    async def test_next_phase_uses_changed_roster(self, tmp_db):
        """Across a phase transition the leaver stays gone, the joiner speaks."""
        disc, mod, (a, b) = _start(tmp_db, "counterfactual")
        c = tmp_db.add_entity("C", "human", "#222222")
        remove_from_discussion(disc, tmp_db, b)
        add_to_discussion(disc, tmp_db, c)
        # Finish extract (moderator-only) so stress_test derives its order.
        disc.method_state["current_phase"] = "extract"
        disc.method_state["claims"] = [{"id": 1, "text": "A claim"}]
        disc.turn_order = [mod]
        disc.current_turn_index = 0
        disc.turn_number = 5

        result = await _complete_turn(disc, tmp_db)

        assert "error" not in result
        assert disc.method_state["current_phase"] == "stress_test"
        assert disc.turn_order == [a, c]

    def test_concluded_record_is_not_rewritten(self, tmp_db):
        """Setup-screen edits to a concluded discussion still held in memory
        are input for the next start; they must not touch its record."""
        disc, _mod, (a, b) = _start(tmp_db)
        disc.status = "concluded"
        tmp_db.update_discussion(disc.id, status="concluded")
        c = tmp_db.add_entity("C", "human", "#222222")

        assert "error" not in set_participant_role(disc, tmp_db, a, DA)
        assert "error" not in add_to_discussion(disc, tmp_db, c)
        assert remove_from_discussion(disc, tmp_db, b) is True

        assert {e.id for e in disc.entities} >= {a, c}
        restored = _reload(tmp_db, disc)
        assert restored.base_turn_order == [a, b]
        assert restored.member_roles[a] != DA


class TestRoleChanges:
    def test_devils_advocate_slot_survives_reload(self, tmp_db):
        disc, _mod, (a, b) = _start(tmp_db)

        set_participant_role(disc, tmp_db, a, DA)

        assert disc.turn_order == [b, a]
        assert disc.base_turn_order == [b, a]
        assert _reload(tmp_db, disc).turn_order == [b, a]

    def test_turn_index_stamp_follows_reorder(self, tmp_db):
        """The stamp is re-pointed at the live index in the new order."""
        disc, _mod, (a, b) = _start(tmp_db)
        disc.current_turn_index = 1  # b's turn
        disc.method_state["_turn_index"] = 1
        disc.method_state["_turn_index_turn"] = disc.turn_number

        set_participant_role(disc, tmp_db, a, DA)  # [b, a]: b now at 0

        assert disc.current_speaker.id == b
        assert disc.method_state["_turn_index"] == 0
        assert _reload(tmp_db, disc).current_speaker.id == b
