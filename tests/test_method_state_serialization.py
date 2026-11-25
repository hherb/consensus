"""``serialize_method_state`` must never replace a method's state (issue #150).

It used to log and return ``"{}"`` when ``method_state`` held a value JSON
cannot encode, and every save path then committed that empty state over the
stored one and reported success: a reload lost the method's bookkeeping and
the user was told nothing.  It now raises, so each save path's existing
error handling reports the failure and the stored state survives.
"""

import json

import pytest

from consensus.app_discussion_setup import add_to_discussion
from consensus.methods import serialize_method_state

from tests.test_app_discussion_rotation import _complete_turn, _start

UNENCODABLE = {1, 2}


def _stored_state(db, disc) -> dict:
    return json.loads(db.get_discussion(disc.id)["method_state"])


def test_encodable_state_round_trips():
    state = {"current_phase": "p", "_turn_order": [1, 2]}
    assert json.loads(serialize_method_state(state)) == state


def test_unencodable_state_raises_instead_of_returning_empty():
    with pytest.raises(ValueError, match="method_state"):
        serialize_method_state({"votes": UNENCODABLE})


def test_rotation_change_reports_error_and_keeps_stored_state(tmp_db):
    """On the rotation path the raise reaches ``update_rotation``'s rollback
    (#147): memory is restored, the error is returned, the record is kept."""
    disc, _mod, (a, b) = _start(tmp_db)
    stored = _stored_state(tmp_db, disc)
    disc.method_state["votes"] = UNENCODABLE
    c = tmp_db.add_entity("C", "human", "#222222")

    result = add_to_discussion(disc, tmp_db, c)

    assert result["error"] == "The change was not saved: ValueError"
    assert c not in [e.id for e in disc.entities]
    assert tmp_db.get_discussion_member(disc.id, c) is None
    assert _stored_state(tmp_db, disc) == stored


async def test_completed_turn_raises_and_keeps_stored_state(tmp_db):
    disc, _mod, _parts = _start(tmp_db, "counterfactual")
    stored = _stored_state(tmp_db, disc)
    disc.method_state["votes"] = UNENCODABLE

    with pytest.raises(ValueError, match="method_state"):
        await _complete_turn(disc, tmp_db)

    assert _stored_state(tmp_db, disc) == stored
