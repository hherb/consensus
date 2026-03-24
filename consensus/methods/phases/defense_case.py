"""Defense case phase handler for Court of Law.

The defense presents its evidence and counter-arguments.  The accusation
side cross-examines after each presentation.
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


class DefenseCaseHandler(PhaseHandler):
    """Phase 4: Defense presents, accusation cross-examines."""

    phase = Phase(
        name="defense_case",
        display_name="Defense Case",
        description=(
            "The defense presents its evidence, counter-arguments, and "
            "rebuttals.  The accusation side may then cross-examine."
        ),
        rounds=2,
    )

    # ── Turn order ────────────────────────────────────────────────────

    def get_turn_order(self, entity_ids: list[int],
                       discussion: Discussion) -> list[int]:
        """Defense presents first, then accusation cross-examines."""
        dfn = get_defense_ids(discussion)
        acc = get_accusation_ids(discussion)
        return dfn + acc

    # ── Prompts ───────────────────────────────────────────────────────

    def get_system_prompt(self, entity: Entity,
                          discussion: Discussion) -> str:
        label = get_accusation_label(discussion)
        trial = get_trial_type(discussion)
        team = get_team_for_entity(entity.id, discussion)

        base = (
            f"You are {entity.name} in a Court of Law "
            f"{'criminal trial' if trial == 'criminal' else 'civil proceeding'}.\n"
            f"Case: {discussion.topic}\n\n"
        )

        if team == "defense":
            return base + (
                "DEFENSE CASE PHASE — You are the Defense.\n\n"
                "Present your defense.  This is your opportunity to:\n"
                "- Provide counter-evidence and alternative explanations\n"
                f"- Rebut the {label}'s key arguments\n"
                "- Present mitigating factors or context\n"
                "- Challenge the strength of the evidence against you\n\n"
                "Build a compelling defense that directly addresses the "
                "charges/claims."
            )
        return base + (
            "DEFENSE CASE PHASE — You are the "
            f"{label} (cross-examination).\n\n"
            "The Defense is presenting their case.  Your role now is "
            "CROSS-EXAMINATION:\n"
            "- Challenge the defense's evidence and reasoning\n"
            "- Find inconsistencies with earlier testimony\n"
            "- Question the credibility of defense arguments\n"
            "- Expose weaknesses in their position\n\n"
            "Focus on undermining their defense, not restating your case."
        )

    def get_turn_prompt(self, entity: Entity,
                        discussion: Discussion) -> str:
        label = get_accusation_label(discussion)
        team = get_team_for_entity(entity.id, discussion)
        if team == "defense":
            return (
                f"Defense ({entity.name}): present your evidence and "
                "arguments in defense of the charges/claims."
            )
        return (
            f"{label} ({entity.name}): cross-examine the defense's "
            "presentation.  Challenge their evidence and reasoning."
        )

    def get_summary_prompt(self, discussion: Discussion,
                           speaker_name: str,
                           next_speaker_name: str) -> str:
        return (
            f"As presiding judge, note the key points from {speaker_name}'s "
            "defense presentation or cross-examination.  Highlight the "
            "strongest arguments and challenges.  "
            f"Next: {next_speaker_name}."
        )

    def get_transition_message(self, discussion: Discussion) -> str:
        label = get_accusation_label(discussion)
        return (
            f"**Phase: {self.phase.display_name}**\n\n"
            f"The Defense will now present their case.  The {label} may "
            "cross-examine after each presentation."
        )
