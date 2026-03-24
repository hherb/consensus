"""Arraignment phase handler for Court of Law.

Charges or claims are formally stated by the accusation side,
and the defense enters its plea or initial response.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..base import Phase
from ..phase_handler import PhaseHandler
from ._court_helpers import (
    get_accusation_ids,
    get_accusation_label,
    get_defense_ids,
    get_team_for_entity,
    get_trial_type,
)

if TYPE_CHECKING:
    from ...models import Discussion, Entity


class ArraignmentHandler(PhaseHandler):
    """Phase 1: Charges read, defense responds."""

    phase = Phase(
        name="arraignment",
        display_name="Arraignment",
        description=(
            "The accusation side formally states the charges or claims.  "
            "The defense then enters a plea or initial response to each."
        ),
        rounds=1,
    )

    # ── Turn order ────────────────────────────────────────────────────

    def get_turn_order(self, entity_ids: list[int],
                       discussion: Discussion) -> list[int]:
        """Accusation first, then defense."""
        acc = get_accusation_ids(discussion)
        dfn = get_defense_ids(discussion)
        return acc + dfn

    # ── Prompts ───────────────────────────────────────────────────────

    def get_system_prompt(self, entity: Entity,
                          discussion: Discussion) -> str:
        label = get_accusation_label(discussion)
        trial = get_trial_type(discussion)
        team = get_team_for_entity(entity.id, discussion)

        base = (
            f"You are {entity.name}, participating in a Court of Law "
            f"{'criminal trial' if trial == 'criminal' else 'civil proceeding'}.\n"
            f"Case: {discussion.topic}\n\n"
        )

        if team == "accusation":
            return base + (
                f"ARRAIGNMENT PHASE — You are the {label}.\n\n"
                "State the charges or claims against the defendant.  "
                "Be specific about each charge/claim and the basis for it.  "
                "Present them in a clear, numbered format."
            )
        return base + (
            "ARRAIGNMENT PHASE — You are the Defense.\n\n"
            f"The {label} has stated the charges/claims against your client.  "
            "Respond to each charge or claim.  Enter your plea or initial "
            "position on each point.  You may reserve detailed arguments "
            "for later phases."
        )

    def get_turn_prompt(self, entity: Entity,
                        discussion: Discussion) -> str:
        label = get_accusation_label(discussion)
        team = get_team_for_entity(entity.id, discussion)
        if team == "accusation":
            return (
                f"{label} ({entity.name}): formally state the charges "
                "or claims.  Be specific and numbered."
            )
        return (
            f"Defense ({entity.name}): respond to the charges.  "
            "State your plea or initial position on each."
        )

    def get_summary_prompt(self, discussion: Discussion,
                           speaker_name: str,
                           next_speaker_name: str) -> str:
        return (
            f"{speaker_name} has presented their position.  "
            "As the presiding judge, note the key charges or claims "
            "stated and the defense's response to each.  "
            f"Next to speak: {next_speaker_name}."
        )

    def get_transition_message(self, discussion: Discussion) -> str:
        label = get_accusation_label(discussion)
        return (
            f"**Phase: {self.phase.display_name}**\n\n"
            f"The {label} will now formally state the charges or claims.  "
            "The Defense will then respond."
        )
