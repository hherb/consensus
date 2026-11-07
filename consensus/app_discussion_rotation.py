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

# Only a live discussion persists rotation changes.  Before start there is
# no record; a concluded one's record is history, and edits made to it on
# the setup screen are input for the next ``start_discussion``.
LIVE_STATUSES = ("active", "paused")


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
    joiner waits until the method next derives the order (normally the
    next phase transition).  The devil's advocate is kept last, the turn
    stays with its holder (or passes on if the holder is removed, see
    :func:`carry_turn_index`), and the result is persisted.  No-op unless
    the discussion is live (``LIVE_STATUSES``): ``start_discussion``
    builds the rotation from scratch.
    """
    if not discussion.id or discussion.status not in LIVE_STATUSES:
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
    # Removing a narrowed phase's only speaker (possible while paused) must
    # not leave it without one; fall back to the roster as
    # ``apply_method_turn_order`` does for an empty method order.
    discussion.turn_order = (changed(old_order, joins_phase)
                             or list(discussion.base_turn_order))
    discussion.current_turn_index = carry_turn_index(
        old_order, discussion.turn_order, discussion.current_turn_index)

    state = discussion.method_state
    if "_turn_order" in state:
        state["_turn_order"] = list(discussion.turn_order)
    # The stamp is a position in the order; re-point it at the live index
    # in the new order so a reload still restores it (issue #19).
    if "_turn_index" in state:
        state["_turn_index"] = discussion.current_turn_index

    db.set_member_turn_positions(discussion.id, discussion.base_turn_order)
    db.update_discussion(
        discussion.id, method_state=serialize_method_state(state))
