"""The speaking rotation after a discussion has started (issue #104).

A started discussion keeps its rotation in four places: ``turn_order``
(the current phase's order), ``base_turn_order`` (the full roster every
method's ``get_turn_order`` hook derives from), ``method_state`` (the
``_turn_order`` / ``_turn_index`` bookkeeping a reload restores) and
``discussion_members.turn_position`` (what a reload rebuilds the roster
from).  Membership and role changes go through :func:`update_rotation`
so the four cannot drift apart.
"""

from typing import Optional

from .app_discussion_flow.helpers import method_roster
from .database import Database
from .methods import serialize_method_state
from .models import Discussion

DEVILS_ADVOCATE = "devils_advocate"


def devils_advocate_last(
    order: list[int], member_roles: dict[int, str],
) -> list[int]:
    """Return ``order`` with any devil's advocate moved to the end."""
    das = [e for e in order if member_roles.get(e) == DEVILS_ADVOCATE]
    return [e for e in order if e not in das] + das


def carry_turn_index(
    old_order: list[int], new_order: list[int], index: int,
) -> int:
    """Return the index in ``new_order`` of whoever holds the turn.

    A reorder never changes whose turn it is.  When the holder has left,
    the turn passes to the next member after them who is still present.
    """
    if not old_order or not new_order:
        return 0
    index %= len(old_order)
    for eid in old_order[index:] + old_order[:index]:
        if eid in new_order:
            return new_order.index(eid)
    return 0


def update_rotation(
    discussion: Discussion,
    db: Database,
    add: Optional[int] = None,
    remove: Optional[int] = None,
) -> None:
    """Apply a membership or role change to every copy of the rotation.

    ``remove`` leaves both orders; ``add`` joins the roster at once but the
    current phase only when that phase runs the whole roster — a narrowed
    phase (moderator-only, red team, ...) was chosen by the method, so the
    joiner waits for the next phase transition.  The devil's advocate is
    kept last, whose turn it is does not change, and the result is
    persisted.  No-op before the discussion starts: ``start_discussion``
    builds the rotation from scratch.
    """
    if not discussion.id:
        return
    roster = method_roster(discussion)
    old_order = list(discussion.turn_order)
    joins_phase = set(old_order) == set(roster)

    def changed(order: list[int], include_add: bool) -> list[int]:
        order = [e for e in order if e != remove]
        if add is not None and include_add and add not in order:
            order.append(add)
        return devils_advocate_last(order, discussion.member_roles)

    discussion.base_turn_order = changed(roster, True)
    discussion.turn_order = changed(old_order, joins_phase)
    discussion.current_turn_index = carry_turn_index(
        old_order, discussion.turn_order, discussion.current_turn_index)

    state = discussion.method_state
    if "_turn_order" in state:
        state["_turn_order"] = list(discussion.turn_order)
    # The stamp is a position in the order; keep it pointing at the same
    # member so a reload still restores the live index (issue #19).
    if "_turn_index" in state:
        state["_turn_index"] = discussion.current_turn_index

    db.set_member_turn_positions(discussion.id, discussion.base_turn_order)
    db.update_discussion(
        discussion.id, method_state=serialize_method_state(state))
