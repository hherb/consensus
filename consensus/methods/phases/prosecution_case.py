"""Prosecution / Plaintiff case phase handler for Court of Law.

The accusation side presents evidence and arguments.  The defense
cross-examines after each presentation.
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


class ProsecutionCaseHandler(PhaseHandler):
    """Phase 3: Accusation presents, defense cross-examines."""

    phase = Phase(
        name="prosecution_case",
        display_name="Prosecution / Plaintiff Case",
        description=(
            "The accusation side presents its evidence and arguments.  "
            "The defense may then cross-examine, challenging claims "
            "and questioning evidence quality."
        ),
        rounds=2,
    )

    # ── Turn order ────────────────────────────────────────────────────

    def get_turn_order(self, entity_ids: list[int],
                       discussion: Discussion) -> list[int]:
        """Accusation presents, then defense cross-examines."""
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
            f"You are {entity.name} in a Court of Law "
            f"{'criminal trial' if trial == 'criminal' else 'civil proceeding'}.\n"
            f"Case: {discussion.topic}\n\n"
        )

        if team == "accusation":
            return base + (
                f"{label.upper()} CASE PHASE — You are the {label}.\n\n"
                "Present your evidence and arguments supporting the case.  "
                "Reference specific charges or claims from the arraignment.  "
                "Build your case methodically:\n"
                "- Present facts and evidence clearly\n"
                "- Connect evidence to specific charges/claims\n"
                "- Anticipate and pre-empt likely defense arguments\n"
                "- Be persuasive but factual"
            )
        return base + (
            f"{label.upper()} CASE PHASE — You are the Defense "
            "(cross-examination).\n\n"
            f"The {label} is presenting their case.  Your role now is "
            "CROSS-EXAMINATION only:\n"
            "- Challenge specific claims made by the prosecution\n"
            "- Question the quality and relevance of evidence presented\n"
            "- Point out gaps in reasoning or logic\n"
            "- Expose weaknesses in their arguments\n\n"
            "Do NOT present your own case yet — save that for the "
            "Defense Case phase.  Focus only on weakening theirs."
        )

    def get_turn_prompt(self, entity: Entity,
                        discussion: Discussion) -> str:
        label = get_accusation_label(discussion)
        team = get_team_for_entity(entity.id, discussion)
        if team == "accusation":
            return (
                f"{label} ({entity.name}): present evidence and arguments "
                "supporting the charges/claims."
            )
        return (
            f"Defense ({entity.name}): cross-examine the {label}'s "
            "presentation.  Challenge specific claims and evidence."
        )

    def get_summary_prompt(self, discussion: Discussion,
                           speaker_name: str,
                           next_speaker_name: str) -> str:
        return (
            f"As presiding judge, note the key points from {speaker_name}'s "
            "presentation or cross-examination.  Highlight the strongest "
            "arguments and any significant challenges raised.  "
            f"Next: {next_speaker_name}."
        )

    def get_transition_message(self, discussion: Discussion) -> str:
        label = get_accusation_label(discussion)
        return (
            f"**Phase: {self.phase.display_name}**\n\n"
            f"The {label} will now present their case.  The Defense may "
            "cross-examine after each presentation."
        )
