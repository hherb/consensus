"""Court of Law helper utilities.

Shared team-resolution and huddle management functions used by all
court phase handlers.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...models import Discussion

# Prefix prepended to huddle messages by process_response.
# filter_context_message uses this to reliably identify and suppress
# huddle messages for non-team members.
HUDDLE_PREFIX = "[PRIVATE HUDDLE] "

# ── Trial type & team resolution ──────────────────────────────────────

def get_trial_type(discussion: Discussion) -> str:
    """Return ``"criminal"`` or ``"civil"`` from method_state."""
    return discussion.method_state.get("trial_type", "criminal")


def get_accusation_ids(discussion: Discussion) -> list[int]:
    """Return entity IDs on the accusation side (prosecutor or plaintiff)."""
    trial_type = get_trial_type(discussion)
    target_roles = ("prosecutor",) if trial_type == "criminal" else ("plaintiff",)
    return [
        eid for eid, role in discussion.member_roles.items()
        if role in target_roles and _in_turn_order(eid, discussion)
    ]


def get_defense_ids(discussion: Discussion) -> list[int]:
    """Return entity IDs on the defense side."""
    return [
        eid for eid, role in discussion.member_roles.items()
        if role == "defense" and _in_turn_order(eid, discussion)
    ]


def get_team_for_entity(entity_id: int, discussion: Discussion) -> str | None:
    """Return ``"accusation"``, ``"defense"``, or ``None``."""
    if entity_id in get_accusation_ids(discussion):
        return "accusation"
    if entity_id in get_defense_ids(discussion):
        return "defense"
    return None


def get_accusation_label(discussion: Discussion) -> str:
    """Return display label for the accusation side."""
    return "Prosecution" if get_trial_type(discussion) == "criminal" else "Plaintiff"


# ── Huddle state management ───────────────────────────────────────────

_HUDDLE_DEFAULTS: dict = {
    "active_team": None,
    "sub_state": "accusation_huddle",
    "huddle_round": 0,
    "spokesperson_id": None,
    "huddle_message_turns": [],
}


def init_huddle_state() -> dict:
    """Return a fresh huddle state dict."""
    return dict(_HUDDLE_DEFAULTS)


def get_huddle_state(discussion: Discussion,
                     key: str = "huddle") -> dict:
    """Return the huddle sub-dict from method_state."""
    return discussion.method_state.get(key, {})


def auto_skip_solo_huddles(discussion: Discussion,
                           key: str = "huddle") -> None:
    """Skip huddle sub-states for teams with one or zero members.

    Call this at phase entry (first ``get_turn_order``) to avoid
    prompting a solo entity with "consult your teammates".
    """
    huddle = discussion.method_state.get(key)
    if not huddle:
        return
    acc_ids = get_accusation_ids(discussion)
    def_ids = get_defense_ids(discussion)
    # Skip accusation huddle if solo
    if huddle["sub_state"] == "accusation_huddle" and len(acc_ids) <= 1:
        huddle["sub_state"] = "accusation_speaks"
        huddle["spokesperson_id"] = acc_ids[0] if acc_ids else None
    # Skip defense huddle if solo
    if huddle["sub_state"] == "defense_huddle" and len(def_ids) <= 1:
        huddle["sub_state"] = "defense_speaks"
        huddle["spokesperson_id"] = def_ids[0] if def_ids else None


def advance_huddle_state(discussion: Discussion,
                         key: str = "huddle") -> None:
    """Advance the huddle sub-state machine one step.

    State machine::

        accusation_huddle (round 1-2) → accusation_speaks →
        defense_huddle (round 1-2) → defense_speaks → done
    """
    huddle = discussion.method_state.setdefault(key, dict(_HUDDLE_DEFAULTS))
    sub = huddle["sub_state"]
    acc_ids = get_accusation_ids(discussion)
    def_ids = get_defense_ids(discussion)

    if sub == "accusation_huddle":
        huddle["active_team"] = "accusation"
        if len(acc_ids) <= 1 or huddle["huddle_round"] >= 2:
            # Skip/finish huddle → spokesperson speaks
            huddle["sub_state"] = "accusation_speaks"
            huddle["huddle_round"] = 0
            if not huddle["spokesperson_id"] and acc_ids:
                huddle["spokesperson_id"] = acc_ids[0]
        else:
            huddle["huddle_round"] += 1

    elif sub == "accusation_speaks":
        # Accusation done → move to defense huddle
        huddle["sub_state"] = "defense_huddle"
        huddle["active_team"] = "defense"
        huddle["huddle_round"] = 0
        huddle["spokesperson_id"] = None

    elif sub == "defense_huddle":
        huddle["active_team"] = "defense"
        if len(def_ids) <= 1 or huddle["huddle_round"] >= 2:
            huddle["sub_state"] = "defense_speaks"
            huddle["huddle_round"] = 0
            if not huddle["spokesperson_id"] and def_ids:
                huddle["spokesperson_id"] = def_ids[0]
        else:
            huddle["huddle_round"] += 1

    elif sub == "defense_speaks":
        huddle["sub_state"] = "done"
        huddle["active_team"] = None

    # else "done" — no-op


def huddle_turn_order(discussion: Discussion,
                      key: str = "huddle") -> list[int]:
    """Return the entity IDs that should speak in the current huddle sub-state."""
    huddle = get_huddle_state(discussion, key)
    sub = huddle.get("sub_state", "done")
    acc_ids = get_accusation_ids(discussion)
    def_ids = get_defense_ids(discussion)

    if sub == "accusation_huddle":
        return acc_ids
    if sub == "accusation_speaks":
        sid = huddle.get("spokesperson_id") or (acc_ids[0] if acc_ids else None)
        return [sid] if sid else acc_ids
    if sub == "defense_huddle":
        return def_ids
    if sub == "defense_speaks":
        sid = huddle.get("spokesperson_id") or (def_ids[0] if def_ids else None)
        return [sid] if sid else def_ids
    # "done" — shouldn't be called, but return everyone
    return acc_ids + def_ids


def is_huddle_message(turn_number: int, discussion: Discussion) -> bool:
    """Check whether a given turn number was a huddle message."""
    huddle = get_huddle_state(discussion)
    return turn_number in huddle.get("huddle_message_turns", [])


def extract_spokesperson(content: str, discussion: Discussion,
                         team: str) -> int | None:
    """Try to extract a spokesperson nomination from a response.

    Looks for ``SPOKESPERSON: <name>`` in *content* and matches against
    entity names on the given *team*.  Returns the entity ID or None.
    """
    m = re.search(r"SPOKESPERSON:\s*(.+)", content, re.IGNORECASE)
    if not m:
        return None
    raw_name = m.group(1).strip().rstrip(".")
    team_ids = (get_accusation_ids(discussion) if team == "accusation"
                else get_defense_ids(discussion))
    for entity in discussion.entities:
        if entity.id in team_ids and raw_name.lower() in entity.name.lower():
            return entity.id
    return None


# ── Huddle privacy filtering (phase-agnostic) ────────────────────────

def filter_huddle_message(entity_name: str, content: str,
                          discussion: Discussion, *,
                          current_entity_id: int | None = None) -> str:
    """Suppress huddle-prefixed messages for non-team readers.

    This is phase-agnostic: it checks the ``HUDDLE_PREFIX`` marker
    regardless of which phase is currently active, so huddle messages
    remain private even in later phases.
    """
    if current_entity_id is None or not content.startswith(HUDDLE_PREFIX):
        return content

    reader_team = get_team_for_entity(current_entity_id, discussion)

    author_id = None
    for e in discussion.entities:
        if e.name == entity_name:
            author_id = e.id
            break

    if author_id is None:
        return content

    author_team = get_team_for_entity(author_id, discussion)

    # Suppress if reader is on a different team or has no team (judge)
    if author_team and author_team != reader_team:
        return ""

    return content


# ── Internal helpers ──────────────────────────────────────────────────

def _in_turn_order(entity_id: int, discussion: Discussion) -> bool:
    """Check whether entity is a participant (not just moderator)."""
    # An entity is a participant if it appears in the original member
    # list regardless of current turn_order (which changes per phase).
    return any(e.id == entity_id for e in discussion.entities
               if e.id != discussion.moderator_id)
