"""Premortem Imagine phase handler for Premortem Analysis.

Each participant imagines the plan has failed spectacularly and
writes a narrative explaining how and why it failed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..base import Phase
from ..phase_handler import PhaseHandler

if TYPE_CHECKING:
    from ...models import Discussion, Entity


class PremortemImagineHandler(PhaseHandler):
    """Phase 2: Premortem failure narratives."""

    phase = Phase(
        name="premortem",
        display_name="Premortem Narratives",
        description=(
            "Imagine we are one year in the future and the plan has "
            "FAILED spectacularly.  Write a narrative explaining how "
            "and why it failed.  Be specific and creative — identify "
            "concrete failure modes, not vague risks."
        ),
        rounds=2,
    )

    # ------------------------------------------------------------------
    # Prompts
    # ------------------------------------------------------------------

    def get_system_prompt(self, entity: Entity,
                          discussion: Discussion) -> str:
        state = discussion.method_state
        conclusion = state.get("conclusion", "")
        base = (
            f"You are {entity.name}, participating in a Premortem Analysis.\n"
            f"Topic: {discussion.topic}\n\n"
        )
        return base + (
            "PREMORTEM PHASE\n\n"
            f"The following conclusion/plan has been tentatively adopted:\n"
            f"  \"{conclusion}\"\n\n"
            "IMAGINE IT IS ONE YEAR FROM NOW AND THIS PLAN HAS FAILED "
            "SPECTACULARLY.\n\n"
            "Your task:\n"
            "1. Write a vivid, specific narrative of HOW it failed\n"
            "2. Identify the root causes — what went wrong and why\n"
            "3. Note any early warning signs that were missed\n"
            "4. Be creative and consider failure modes others might overlook\n\n"
            "Do NOT hedge or soften — the plan has ALREADY failed in this "
            "scenario.  Your job is to explain why, not whether."
        )

    def get_turn_prompt(self, entity: Entity,
                        discussion: Discussion) -> str:
        round_num = discussion.method_state.get("phase_round", 1)
        if round_num == 1:
            return (
                f"It is your turn, {entity.name}.  The plan has failed.  "
                "Write your failure narrative — be specific and creative."
            )
        return (
            f"Round {round_num}, {entity.name}.  Having seen others' "
            "failure narratives, identify additional failure modes they "
            "missed, or elaborate on the most dangerous ones."
        )

    def get_summary_prompt(self, discussion: Discussion,
                           speaker_name: str,
                           next_speaker_name: str) -> str:
        return (
            f"{speaker_name} has presented their failure narrative.  "
            "Briefly note the key failure modes identified and how "
            "they differ from previously raised concerns.  "
            f"Next: {next_speaker_name}."
        )

    # ------------------------------------------------------------------
    # Transition
    # ------------------------------------------------------------------

    def get_transition_message(self, discussion: Discussion) -> str:
        state = discussion.method_state
        conclusion = state.get("conclusion", "")
        return (
            f"**Phase: {self.phase.display_name}**\n\n"
            "The following plan/conclusion will be subjected to "
            "premortem analysis:\n\n"
            f"> {conclusion}\n\n"
            "Imagine it is one year from now and this plan has "
            "**failed spectacularly**.  Each participant will explain "
            "how and why it failed."
        )
