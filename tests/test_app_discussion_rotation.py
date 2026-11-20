"""Tests for the post-start speaking rotation (issue #104).

Mid-discussion membership and role changes used to update
``discussion.turn_order`` in memory only, so a removed member came back at
the next phase transition (``base_turn_order`` still held them), a late
joiner was skipped, and a reload restored the devil's advocate's old slot
(``turn_position`` was never rewritten).  These drive the real setup,
``complete_turn`` and ``load_discussion`` paths.
"""

import logging
import sqlite3

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
from consensus.methods import serialize_method_state
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


class TestRotationIsOneTransaction:
    """A rotation change is one commit (issue #107): a failure partway
    through used to leave a joiner with no ``turn_position``, or a saved
    ``_turn_order`` still naming a leaver.  A failed commit also leaves the
    in-memory ``Discussion`` as it was and is reported as an ``error``
    (issue #147): it used to raise after memory had already changed, so the
    live session kept a joiner the record never got."""

    @staticmethod
    def _fail_method_state_writes(db) -> None:
        """Make the last write of a rotation change fail inside SQLite."""
        with db._lock:
            db.conn.execute(
                "CREATE TRIGGER fail_method_state BEFORE UPDATE OF "
                "method_state ON discussions "
                "BEGIN SELECT RAISE(ABORT, 'disk on fire'); END")
            db.conn.commit()

    @staticmethod
    def _memory(disc: Discussion) -> tuple:
        """Everything a rotation change touches in memory."""
        return ([e.id for e in disc.entities], dict(disc.member_roles),
                disc.moderator_id, list(disc.turn_order),
                list(disc.base_turn_order), disc.current_turn_index,
                dict(disc.method_state), len(disc.messages))

    def _assert_failed(self, result, disc: Discussion, before: tuple) -> None:
        assert "disk on fire" in result["error"]
        assert self._memory(disc) == before

    def test_failed_join_leaves_no_member_row(self, tmp_db):
        disc, _mod, (a, b) = _start(tmp_db)
        c = tmp_db.add_entity("C", "human", "#222222")
        self._fail_method_state_writes(tmp_db)
        before = self._memory(disc)

        self._assert_failed(add_to_discussion(disc, tmp_db, c), disc, before)

        assert tmp_db.get_discussion_member(disc.id, c) is None
        assert _reload(tmp_db, disc).base_turn_order == [a, b]

    def test_failed_leave_keeps_member_row(self, tmp_db):
        disc, _mod, (a, b, c) = _start(tmp_db, n=3)
        disc.turn_order = [a, b]
        disc.method_state["_turn_order"] = [a, b]
        tmp_db.update_discussion(
            disc.id, method_state=serialize_method_state(disc.method_state))
        self._fail_method_state_writes(tmp_db)
        before = self._memory(disc)

        self._assert_failed(
            remove_from_discussion(disc, tmp_db, b), disc, before)

        assert tmp_db.get_discussion_member(disc.id, b) is not None
        restored = _reload(tmp_db, disc)
        assert restored.turn_order == [a, b]
        assert restored.base_turn_order == [a, b, c]

    def test_failed_role_change_keeps_role_and_slot(self, tmp_db):
        disc, _mod, (a, b) = _start(tmp_db)
        self._fail_method_state_writes(tmp_db)
        before = self._memory(disc)

        self._assert_failed(
            set_participant_role(disc, tmp_db, a, DA), disc, before)

        member = tmp_db.get_discussion_member(disc.id, a)
        assert member["participant_role"] == "standard"
        assert member["turn_position"] == 0
        assert tmp_db.get_entity_tool(a, "web_search") is None

    def test_failed_da_swap_keeps_old_da(self, tmp_db):
        disc, _mod, (a, b) = _start(tmp_db)
        set_participant_role(disc, tmp_db, a, DA)
        self._fail_method_state_writes(tmp_db)
        before = self._memory(disc)

        self._assert_failed(
            set_participant_role(disc, tmp_db, b, DA), disc, before)

        assert tmp_db.get_discussion_member(
            disc.id, a)["participant_role"] == DA
        assert tmp_db.get_discussion_member(
            disc.id, b)["participant_role"] == "standard"

    def test_failed_da_join_keeps_old_da(self, tmp_db):
        disc, _mod, (a, b) = _start(tmp_db)
        set_participant_role(disc, tmp_db, a, DA)
        c = tmp_db.add_entity("C", "human", "#222222")
        self._fail_method_state_writes(tmp_db)
        before = self._memory(disc)

        self._assert_failed(
            add_to_discussion(disc, tmp_db, c, participant_role=DA),
            disc, before)

        assert tmp_db.get_discussion_member(
            disc.id, a)["participant_role"] == DA
        assert tmp_db.get_entity_tool(c, "web_search") is None

    def test_unsaved_join_notice_keeps_the_saved_change(self, tmp_db):
        """The rotation is committed before the join notice is written, so
        a failed notice write must not report the join as failed."""
        disc, _mod, (a, b) = _start(tmp_db)
        c = tmp_db.add_entity("C", "human", "#222222")

        def broken(*_args, **_kwargs):
            raise sqlite3.OperationalError("disk full")
        tmp_db.add_message = broken

        result = add_to_discussion(disc, tmp_db, c)

        assert "error" not in result
        assert disc.messages[-1].content.startswith("-- C joined")
        assert "could not be saved" in disc.messages[-1].content
        assert _reload(tmp_db, disc).base_turn_order == [a, b, c]

    def test_unsaved_leave_notice_keeps_the_saved_change(self, tmp_db):
        disc, _mod, (a, b, c) = _start(tmp_db, n=3)

        def broken(*_args, **_kwargs):
            raise sqlite3.OperationalError("disk full")
        tmp_db.add_message = broken

        assert remove_from_discussion(disc, tmp_db, b) is True

        assert disc.messages[-1].content.startswith(
            "-- P2 left the discussion --")
        assert "could not be saved" in disc.messages[-1].content
        assert tmp_db.get_discussion_member(disc.id, b) is None
        assert _reload(tmp_db, disc).base_turn_order == [a, c]

    def test_failed_moderator_join_keeps_old_moderator(self, tmp_db):
        disc, mod, _parts = _start(tmp_db)
        new_mod = tmp_db.add_entity("M2", "human", "#444444")
        self._fail_method_state_writes(tmp_db)
        before = self._memory(disc)

        self._assert_failed(
            add_to_discussion(disc, tmp_db, new_mod, is_moderator=True),
            disc, before)

        assert disc.moderator_id == mod
        assert tmp_db.get_discussion_member(disc.id, new_mod) is None

    @pytest.mark.parametrize("via", ["role", "join"])
    def test_da_tool_failure_after_save_is_reported(self, tmp_db, via):
        """The DA tools are assigned after the rotation is committed, so a
        failed tool write must not report the saved change as failed."""
        disc, _mod, (a, b) = _start(tmp_db)

        def broken(*_args, **_kwargs):
            raise sqlite3.OperationalError("database is locked")
        tmp_db.add_entity_tool = broken

        if via == "role":
            da, result = a, set_participant_role(disc, tmp_db, a, DA)
        else:
            da = tmp_db.add_entity("C", "human", "#222222")
            result = add_to_discussion(disc, tmp_db, da, participant_role=DA)

        assert "error" not in result
        assert tmp_db.get_discussion_member(
            disc.id, da)["participant_role"] == DA
        assert "database is locked" in disc.messages[-1].content

    def test_joiner_role_is_persisted(self, tmp_db):
        disc, _mod, (a, b) = _start(tmp_db)
        c = tmp_db.add_entity("C", "human", "#222222")

        add_to_discussion(disc, tmp_db, c, participant_role=DA)

        assert tmp_db.get_discussion_member(
            disc.id, c)["participant_role"] == DA
        restored = _reload(tmp_db, disc)
        assert restored.member_roles[c] == DA
        assert restored.base_turn_order == [a, b, c]

    def test_drift_is_logged(self, tmp_db, caplog):
        disc, _mod, (a, b) = _start(tmp_db)
        stranger = tmp_db.add_entity("S", "human", "#333333")

        with caplog.at_level(logging.WARNING, logger="consensus.db"):
            tmp_db.save_rotation(disc.id, [a, stranger, b], "{}")

        assert "names 3 members but only 2" in caplog.text
        assert f"missing: [{stranger}]" in caplog.text
