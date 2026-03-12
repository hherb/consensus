"""Intake phase handler for Guided Triage.

Moderator asks human participants structured questions about the
problem type, decision context, and uncertainty structure.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..base import Phase
from ..phase_handler import PhaseHandler

if TYPE_CHECKING:
    from ...models import Discussion, Entity, EntityType


class TriageIntakeHandler(PhaseHandler):
    """Phase 1: Moderator interviews human participants."""

    phase = Phase(
        name="intake",
        display_name="Problem Intake",
        description=(
            "The moderator asks structured questions to understand "
            "the nature of the problem before recommending a method."
        ),
        rounds=1,
        allow_tools=False,
    )

    def get_turn_order(self, entity_ids: list[int],
                       discussion: Discussion) -> list[int]:
        """Only human participants respond during intake."""
        from ...models import EntityType
        return [
            eid for eid in entity_ids
            if any(
                e.id == eid and e.entity_type == EntityType.HUMAN
                for e in discussion.entities
            )
        ]

    def should_advance(self, discussion: Discussion) -> bool:
        """Advance after 1 round, or immediately if no humans."""
        from ...models import EntityType
        has_humans = any(
            e.entity_type == EntityType.HUMAN
            and e.id != discussion.moderator_id
            for e in discussion.entities
        )
        if not has_humans:
            return True
        return super().should_advance(discussion)

    def get_system_prompt(self, entity: Entity,
                          discussion: Discussion) -> str:
        return (
            f"You are {entity.name}, participating in a structured "
            f"methodology selection process.\n"
            f"Topic: {discussion.topic}\n\n"
            "The moderator will ask you questions to understand the "
            "nature of this problem so the best discussion method "
            "can be selected."
        )

    def get_turn_prompt(self, entity: Entity,
                        discussion: Discussion) -> str:
        return (
            f"Please answer the following questions about the topic "
            f"\"{discussion.topic}\" to help select the best discussion "
            f"method:\n\n"
            "1. **Type of question:** What kind of question is this? "
            "(e.g., exploring perspectives, making a decision, "
            "forecasting, identifying risks, testing a hypothesis, "
            "resolving a disagreement)\n\n"
            "2. **Decision context:** What is the context? "
            "(e.g., academic exploration, real-world decision with "
            "stakes, risk assessment, policy evaluation)\n\n"
            "3. **Uncertainty structure:** What does the uncertainty "
            "look like? (e.g., known unknowns, expert disagreement, "
            "quantifiable uncertainty, poorly defined problem space)\n\n"
            "4. **Preliminary conclusion:** Is there an existing "
            "conclusion or position to examine? (optional — say "
            "'none' if not applicable)"
        )

    def get_summary_prompt(self, discussion: Discussion,
                           speaker_name: str,
                           next_speaker_name: str) -> str:
        return (
            f"{speaker_name} has provided their problem characterization. "
            "Briefly note the key points about the problem type, context, "
            f"and uncertainty structure. Next: {next_speaker_name}."
        )
