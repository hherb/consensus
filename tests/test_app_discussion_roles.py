"""Characterization tests for participant roles (devil's advocate handling).

Covers ``set_participant_role``, ``auto_assign_da_tools``,
``reorder_da_in_turn_order`` and the devil's-advocate branch of
``add_to_discussion`` — the net built before these moved out of
``app_discussion_setup`` (issue #61).
"""

import pytest

from consensus.app_discussion_roles import (
    _DA_TOOL_NAMES,
    auto_assign_da_tools,
    reorder_da_in_turn_order,
    set_participant_role,
)
from consensus.app_discussion_setup import add_to_discussion
from consensus.models import Discussion

DA = "devils_advocate"


@pytest.fixture
def carol(tmp_db):
    """A third (human) entity, so a discussion can hold two non-moderators."""
    return tmp_db.add_entity("Carol", "human", "#0000ff")


@pytest.fixture
def setup_disc(tmp_db, sample_ai_entity, sample_human_entity, carol):
    """An unstarted discussion: Alice moderates, Bob and Carol participate."""
    disc = Discussion(topic="t")
    add_to_discussion(disc, tmp_db, sample_ai_entity, is_moderator=True)
    add_to_discussion(disc, tmp_db, sample_human_entity)
    add_to_discussion(disc, tmp_db, carol)
    return disc


@pytest.fixture
def active_disc(tmp_db, setup_disc, sample_ai_entity, sample_human_entity,
                carol):
    """``setup_disc`` persisted as an active discussion with a turn order."""
    did = tmp_db.create_discussion("t", sample_ai_entity)
    tmp_db.add_discussion_member(did, sample_ai_entity, is_moderator=True,
                                 also_participant=False)
    for pos, eid in enumerate((sample_human_entity, carol)):
        tmp_db.add_discussion_member(did, eid, turn_position=pos)
    setup_disc.id = did
    setup_disc.status = "active"
    setup_disc.turn_order = [sample_human_entity, carol]
    return setup_disc


def _db_roles(db, discussion_id: int) -> dict[int, str]:
    """Return ``{entity_id: participant_role}`` as persisted."""
    return {m["entity_id"]: m["participant_role"]
            for m in db.get_discussion_members(discussion_id)}


def _tool_names(db, entity_id: int) -> set[str]:
    """Return the names of the tools assigned to an entity."""
    return {t["tool_name"] for t in db.get_entity_tools(entity_id)}


class TestSetParticipantRole:
    def test_entity_not_in_discussion(self, tmp_db, setup_disc):
        assert set_participant_role(setup_disc, tmp_db, 9999, DA) == {
            "error": "Entity not in discussion"}

    def test_moderator_rejected(self, tmp_db, setup_disc, sample_ai_entity):
        result = set_participant_role(setup_disc, tmp_db, sample_ai_entity, DA)
        assert result == {"error": "Cannot assign a role to the moderator"}
        assert setup_disc.member_roles[sample_ai_entity] == "standard"

    def test_sets_role_and_returns_entity(self, tmp_db, setup_disc,
                                          sample_human_entity):
        result = set_participant_role(setup_disc, tmp_db,
                                      sample_human_entity, DA)
        assert result["id"] == sample_human_entity
        assert setup_disc.member_roles[sample_human_entity] == DA

    def test_da_gets_tools(self, tmp_db, setup_disc, sample_human_entity):
        set_participant_role(setup_disc, tmp_db, sample_human_entity, DA)
        assert _tool_names(tmp_db, sample_human_entity) == set(_DA_TOOL_NAMES)

    def test_non_da_role_gets_no_tools(self, tmp_db, setup_disc,
                                       sample_human_entity):
        set_participant_role(setup_disc, tmp_db, sample_human_entity,
                             "prosecutor")
        assert _tool_names(tmp_db, sample_human_entity) == set()

    def test_second_da_reverts_first(self, tmp_db, setup_disc,
                                     sample_human_entity, carol):
        set_participant_role(setup_disc, tmp_db, sample_human_entity, DA)
        set_participant_role(setup_disc, tmp_db, carol, DA)
        assert setup_disc.member_roles[sample_human_entity] == "standard"
        assert setup_disc.member_roles[carol] == DA

    def test_unstarted_discussion_has_no_turn_order_to_touch(
            self, tmp_db, setup_disc, sample_human_entity):
        set_participant_role(setup_disc, tmp_db, sample_human_entity, DA)
        assert setup_disc.turn_order == []

    def test_active_da_moved_last(self, tmp_db, active_disc,
                                  sample_human_entity, carol):
        set_participant_role(active_disc, tmp_db, sample_human_entity, DA)
        assert active_disc.turn_order == [carol, sample_human_entity]

    def test_active_role_change_persisted(self, tmp_db, active_disc,
                                          sample_human_entity, carol):
        set_participant_role(active_disc, tmp_db, sample_human_entity, DA)
        set_participant_role(active_disc, tmp_db, carol, DA)
        roles = _db_roles(tmp_db, active_disc.id)
        assert roles[sample_human_entity] == "standard"
        assert roles[carol] == DA


class TestAutoAssignDaTools:
    def test_is_idempotent(self, tmp_db, sample_human_entity):
        auto_assign_da_tools(tmp_db, sample_human_entity)
        auto_assign_da_tools(tmp_db, sample_human_entity)
        assert len(tmp_db.get_entity_tools(sample_human_entity)) == len(
            _DA_TOOL_NAMES)

    def test_keeps_existing_access_mode(self, tmp_db, sample_human_entity):
        tmp_db.add_entity_tool(sample_human_entity, "web_search", "shared")
        auto_assign_da_tools(tmp_db, sample_human_entity)
        assert tmp_db.get_entity_tool(
            sample_human_entity, "web_search")["access_mode"] == "shared"


class TestReorderDaTurnOrder:
    def test_da_moved_to_end(self):
        disc = Discussion(turn_order=[1, 2, 3], current_turn_index=0)
        disc.member_roles = {1: "standard", 2: "devils_advocate", 3: "standard"}
        reorder_da_in_turn_order(disc)
        assert disc.turn_order[-1] == 2

    def test_no_da_is_noop(self):
        disc = Discussion(turn_order=[1, 2, 3], current_turn_index=0)
        disc.member_roles = {1: "standard", 2: "standard", 3: "standard"}
        reorder_da_in_turn_order(disc)
        assert disc.turn_order == [1, 2, 3]

    def test_turn_index_kept_in_range(self):
        disc = Discussion(turn_order=[1, 2, 3], current_turn_index=2)
        disc.member_roles = {1: DA, 2: "standard", 3: "standard"}
        reorder_da_in_turn_order(disc)
        assert disc.turn_order == [2, 3, 1]
        assert 0 <= disc.current_turn_index < len(disc.turn_order)

    def test_da_outside_turn_order_is_noop(self):
        disc = Discussion(turn_order=[2, 3], current_turn_index=0)
        disc.member_roles = {1: DA, 2: "standard", 3: "standard"}
        reorder_da_in_turn_order(disc)
        assert disc.turn_order == [2, 3]


class TestAddToDiscussionDevilsAdvocate:
    def test_new_da_reverts_existing_and_gets_tools(
            self, tmp_db, sample_ai_entity, sample_human_entity, carol):
        disc = Discussion(topic="t")
        add_to_discussion(disc, tmp_db, sample_ai_entity, is_moderator=True)
        add_to_discussion(disc, tmp_db, sample_human_entity,
                          participant_role=DA)
        add_to_discussion(disc, tmp_db, carol, participant_role=DA)
        assert disc.member_roles[sample_human_entity] == "standard"
        assert disc.member_roles[carol] == DA
        assert _tool_names(tmp_db, carol) == set(_DA_TOOL_NAMES)

    def test_mid_discussion_revert_is_persisted(
            self, tmp_db, active_disc, sample_human_entity):
        """A DA joining mid-discussion demotes the old DA in the DB too.

        Otherwise a reload restores two devil's advocates.
        """
        set_participant_role(active_disc, tmp_db, sample_human_entity, DA)
        dave = tmp_db.add_entity("Dave", "human", "#00ffff")
        add_to_discussion(active_disc, tmp_db, dave, participant_role=DA)
        roles = _db_roles(tmp_db, active_disc.id)
        assert roles[sample_human_entity] == "standard"
        assert roles[dave] == DA
