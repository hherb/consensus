"""Revise phase handler for Red Team / Blue Team.

Blue Team revises their position based on attacks that could not
be adequately defended.  Red Team is silent and excluded from turn order.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..base import Phase
from ..phase_handler import PhaseHandler

if TYPE_CHECKING:
    from ...models import Discussion, Entity


class ReviseRedTeamHandler(PhaseHandler):
    """Phase 3: Blue Team revises (Red Team silent)."""

    phase = Phase(
        name="revise",
        display_name="Revision",
        description=(
            "Blue Team revises the position based on attacks that "
            "could not be adequately defended."
        ),
        rounds=1,
    )

    # ------------------------------------------------------------------
    # Turn order
    # ------------------------------------------------------------------

    def get_turn_order(self, entity_ids: list[int],
                       discussion: Discussion) -> list[int]:
        """Exclude Red Team from revision."""
        state = discussion.method_state
        red_id = state.get("red_team_entity_id")
        return [eid for eid in entity_ids if eid != red_id]

    # ------------------------------------------------------------------
    # Prompts
    # ------------------------------------------------------------------

    def get_system_prompt(self, entity: Entity,
                          discussion: Discussion) -> str:
        state = discussion.method_state
        red_id = state.get("red_team_entity_id")
        is_red = (entity.id == red_id)

        base = (
            f"You are {entity.name}, participating in a Red Team / Blue "
            f"Team analysis.\n"
            f"Topic: {discussion.topic}\n\n"
        )

        if is_red:
            return base + (
                "You are the RED TEAM.  You are SILENT during revision.  "
                "The Blue Team is revising their position based on your "
                "attacks."
            )
        return base + (
            "REVISION PHASE — You are BLUE TEAM.\n\n"
            "Revise your position based on the Red Team attacks that "
            "could not be adequately defended.  Specifically:\n"
            "1. What has changed in your position?\n"
            "2. What attacks were you unable to defend?\n"
            "3. What remains unchanged and why?\n\n"
            "Be honest about weaknesses exposed."
        )

    def get_turn_prompt(self, entity: Entity,
                        discussion: Discussion) -> str:
        return (
            f"Blue Team ({entity.name}): revise the position based "
            "on attacks that succeeded."
        )

    def get_summary_prompt(self, discussion: Discussion,
                           speaker_name: str,
                           next_speaker_name: str) -> str:
        return (
            f"{speaker_name} has presented their revisions.  Note what "
            f"changed and what remained.  Next: {next_speaker_name}."
        )

    # ------------------------------------------------------------------
    # Transition
    # ------------------------------------------------------------------

    def get_transition_message(self, discussion: Discussion) -> str:
        return (
            f"**Phase: {self.phase.display_name}**\n\n"
            "The attack/defense round is over.  Blue Team members "
            "will now revise their position, incorporating attacks "
            "they could not adequately defend."
        )
