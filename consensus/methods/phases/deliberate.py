"""Deliberation phase handler for Voting Method.

Open discussion where participants may propose motions via JSON blocks.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ..base import Phase, ProcessedResponse
from ..phase_handler import PhaseHandler
from ._voting_helpers import (
    DEFAULT_DELIBERATION_ROUNDS,
    extract_motions,
    format_motions,
)

if TYPE_CHECKING:
    from ...models import Discussion, Entity

logger = logging.getLogger(__name__)


class DeliberateHandler(PhaseHandler):
    """Phase 1: Open deliberation with motion proposals."""

    phase = Phase(
        name="deliberate",
        display_name="Deliberation",
        description=(
            "Discuss the topic and propose motions for the group to "
            "vote on.  Include a JSON block to propose a motion."
        ),
        rounds=DEFAULT_DELIBERATION_ROUNDS,
    )

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    def init_state(self, discussion: Discussion) -> dict:
        return {
            "motions": [],
            "votes": [],
            "next_motion_id": 1,
            "threshold": "simple_majority",
        }

    # ------------------------------------------------------------------
    # Prompts
    # ------------------------------------------------------------------

    def get_system_prompt(self, entity: Entity,
                          discussion: Discussion) -> str:
        state = discussion.method_state
        motions_text = format_motions(state)
        return (
            f"You are {entity.name}, participating in a structured "
            f"deliberation with voting.\n"
            f"Topic: {discussion.topic}\n\n"
            "DELIBERATION PHASE\n\n"
            "Discuss the topic and, when ready, propose motions for "
            "the group to vote on.  To propose a motion, include a "
            "JSON block in your response:\n"
            "```json\n"
            '{"motion": "Your proposed motion text"}\n'
            "```\n\n"
            "You may propose multiple motions across your turns.  "
            "Focus on clear, actionable proposals.\n\n"
            f"Current motions on the table:\n{motions_text}"
        )

    def get_turn_prompt(self, entity: Entity,
                        discussion: Discussion) -> str:
        return (
            f"It is your turn, {entity.name}.  Share your thoughts "
            "on the topic.  If you have a clear proposal, include a "
            "motion in a JSON block."
        )

    def get_summary_prompt(self, discussion: Discussion,
                           speaker_name: str,
                           next_speaker_name: str) -> str:
        state = discussion.method_state
        n_motions = len(state.get("motions", []))
        return (
            f"{speaker_name} has spoken.  "
            f"There are currently {n_motions} motion(s) on the table.  "
            f"Briefly summarise the key points and invite "
            f"{next_speaker_name} to continue the deliberation."
        )

    # ------------------------------------------------------------------
    # Response processing
    # ------------------------------------------------------------------

    def process_response(self, content: str, entity: Entity,
                         discussion: Discussion) -> ProcessedResponse:
        state = discussion.method_state
        motions = extract_motions(content)
        extracted: dict = {}

        for motion_text in motions:
            motion_id = state.get("next_motion_id", 1)
            state["motions"].append({
                "id": motion_id,
                "text": motion_text,
                "proposed_by": entity.name,
            })
            state["next_motion_id"] = motion_id + 1
            extracted["motions"] = motions

        if motions:
            motion_lines = "\n".join(
                f"  - Motion {state['motions'][-len(motions)+i]['id']}: {m}"
                for i, m in enumerate(motions)
            )
            content += f"\n\n---\n**Motions proposed:**\n{motion_lines}"

        return ProcessedResponse(
            display_content=content,
            extracted_data=extracted,
        )
