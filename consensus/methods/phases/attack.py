"""Attack phase handler for Red Team / Blue Team.

The Red Team member attacks the Blue Team's constructed position,
then Blue Team members defend.  Red Team goes first in turn order.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..base import Phase
from ..phase_handler import PhaseHandler

if TYPE_CHECKING:
    from ...models import Discussion, Entity


class AttackHandler(PhaseHandler):
    """Phase 2: Red Team attacks, Blue Team defends."""

    phase = Phase(
        name="attack",
        display_name="Red Team Attack",
        description=(
            "The Red Team member attacks the constructed position, "
            "identifying weaknesses, logical flaws, missing evidence, "
            "and alternative explanations.  Blue Team then defends."
        ),
        rounds=2,
    )

    # ------------------------------------------------------------------
    # Turn order
    # ------------------------------------------------------------------

    def get_turn_order(self, entity_ids: list[int],
                       discussion: Discussion) -> list[int]:
        """Red Team first, then Blue Team defends."""
        state = discussion.method_state
        red_id = state.get("red_team_entity_id")

        if red_id is None:
            return entity_ids

        # Red Team first, then Blue Team defends
        blue = [eid for eid in entity_ids if eid != red_id]
        return [red_id] + blue if red_id in entity_ids else entity_ids

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
                "RED TEAM ATTACK PHASE\n\n"
                "You are the Red Team.  Your ONLY job is DESTRUCTION, "
                "not construction.  Attack the Blue Team's position:\n\n"
                "1. **Logical flaws** — Find contradictions, non "
                "sequiturs, circular reasoning\n"
                "2. **Missing evidence** — What did they fail to "
                "consider?\n"
                "3. **Alternative explanations** — What other "
                "conclusions fit the same evidence?\n"
                "4. **Weak assumptions** — What are they taking for "
                "granted?\n"
                "5. **Edge cases** — Where does their position break "
                "down?\n\n"
                "Be aggressive and thorough.  You succeed by finding "
                "genuine weaknesses, not by being contrarian."
            )
        return base + (
            "DEFENSE PHASE — You are BLUE TEAM.\n\n"
            "The Red Team has attacked your position.  Defend against "
            "the attacks:\n"
            "- For attacks you can rebut: provide counter-evidence or "
            "reasoning\n"
            "- For attacks you cannot rebut: acknowledge the weakness "
            "honestly\n\n"
            "Do NOT dismiss attacks without substantive response."
        )

    def get_turn_prompt(self, entity: Entity,
                        discussion: Discussion) -> str:
        state = discussion.method_state
        red_id = state.get("red_team_entity_id")
        is_red = (entity.id == red_id)

        if is_red:
            return (
                f"Red Team ({entity.name}): attack the Blue Team's "
                "position.  Find every weakness you can."
            )
        return (
            f"Blue Team ({entity.name}): defend against the Red "
            "Team's attacks."
        )

    def get_summary_prompt(self, discussion: Discussion,
                           speaker_name: str,
                           next_speaker_name: str) -> str:
        return (
            f"{speaker_name} has made their contribution (attack or "
            "defense).  Note the strongest points raised.  "
            f"Next: {next_speaker_name}."
        )

    # ------------------------------------------------------------------
    # Transition
    # ------------------------------------------------------------------

    def get_transition_message(self, discussion: Discussion) -> str:
        state = discussion.method_state
        red_id = state.get("red_team_entity_id")

        # Find the red team entity name from the discussion
        red_name = f"Entity {red_id}"
        for e in discussion.entities:
            if e.id == red_id:
                red_name = e.name
                break
        return (
            f"**Phase: {self.phase.display_name}**\n\n"
            f"**{red_name}** is the Red Team this round.  "
            "They will attack the Blue Team's position, then the "
            "Blue Team will defend."
        )
