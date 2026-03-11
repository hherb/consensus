"""Construct phase handler for Red Team / Blue Team.

Blue Team members build an initial position.  The Red Team member
is silent during this phase and excluded from the turn order.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..base import Phase
from ..phase_handler import PhaseHandler

if TYPE_CHECKING:
    from ...models import Discussion, Entity


class ConstructHandler(PhaseHandler):
    """Phase 1: Blue Team constructs a position (Red Team silent)."""

    phase = Phase(
        name="construct",
        display_name="Construction",
        description=(
            "Blue Team members build an initial position or analysis.  "
            "The designated Red Team member is silent during this phase."
        ),
        rounds=1,
    )

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    def init_state(self, discussion: Discussion) -> dict:
        return {
            "red_team_entity_id": None,
            "red_team_rotation": 0,
            "attacks": [],
        }

    # ------------------------------------------------------------------
    # Turn order
    # ------------------------------------------------------------------

    def get_turn_order(self, entity_ids: list[int],
                       discussion: Discussion) -> list[int]:
        """Exclude Red Team from construction.  Assign red team if needed."""
        state = discussion.method_state
        red_id = state.get("red_team_entity_id")

        if red_id is None:
            # Assign the first Red Team member
            if entity_ids:
                red_id = entity_ids[0]
                state["red_team_entity_id"] = red_id

        # Exclude Red Team
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
                "You are the RED TEAM this round.  You are SILENT "
                "during the construction phase.  Wait for your turn "
                "to attack."
            )
        return base + (
            "CONSTRUCTION PHASE — You are BLUE TEAM.\n\n"
            "Build a well-reasoned position or analysis on the topic.  "
            "Present your best arguments, evidence, and reasoning.  "
            "The Red Team cannot see or participate in construction — "
            "they will only see your final position."
        )

    def get_turn_prompt(self, entity: Entity,
                        discussion: Discussion) -> str:
        return (
            f"Blue Team member {entity.name}: present your analysis "
            "and position on the topic."
        )

    def get_summary_prompt(self, discussion: Discussion,
                           speaker_name: str,
                           next_speaker_name: str) -> str:
        return (
            f"{speaker_name} has presented their contribution to the "
            "position.  Note the key arguments and evidence.  "
            f"Next: {next_speaker_name}."
        )
