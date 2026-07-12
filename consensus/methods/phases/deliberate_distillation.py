"""Rich Deliberation phase handler for Recursive Self-Distillation.

Open discussion encouraging expressive, detailed reasoning.
The moderator's final-round summary is captured as the
``rich_reasoning_summary`` for later comparison with the
distilled logical skeleton.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..base import Phase
from ..phase_handler import PhaseHandler

if TYPE_CHECKING:
    from ...models import Discussion, Entity


class DistillationDeliberateHandler(PhaseHandler):
    """Phase 1: Rich deliberation — maximise expressiveness."""

    phase = Phase(
        name="sd_deliberate",
        display_name="Rich Deliberation",
        description=(
            "Open discussion where participants are encouraged to provide "
            "detailed reasoning with examples, analogies, and persuasive "
            "arguments. The goal is to develop the strongest possible case "
            "for each position."
        ),
        rounds=2,
        allow_tools=True,
    )

    def init_state(self, discussion: Discussion) -> dict:
        return {"rich_reasoning_summary": None}

    def get_system_prompt(self, entity: Entity,
                          discussion: Discussion) -> str:
        return (
            f"You are {entity.name}, participating in an in-depth "
            f"discussion.\n"
            f"Topic: {discussion.topic}\n\n"
            "Present your MOST COMPELLING case. Use concrete examples, "
            "vivid analogies, historical precedents, expert testimony, "
            "and any rhetorical device that strengthens your argument. "
            "Be thorough and persuasive — the quality of the subsequent "
            "analysis depends on the richness of reasoning produced here.\n\n"
            "Engage deeply with others' arguments. Challenge weak points, "
            "build on strong ones, and work toward the most well-supported "
            "position you can articulate."
        )

    def get_turn_prompt(self, entity: Entity,
                        discussion: Discussion) -> str:
        return (
            f"It is your turn, {entity.name}. Present your most detailed "
            "and persuasive reasoning. Use examples, analogies, evidence, "
            "and any other rhetorical tools to make your case as compelling "
            "as possible. Engage with what others have said."
        )

    def get_summary_prompt(self, discussion: Discussion,
                           speaker_name: str,
                           next_speaker_name: str) -> str:
        return (
            f"{speaker_name} has presented their argument. "
            "Capture the full richness of their reasoning — the examples, "
            "analogies, and rhetorical force — not just the conclusion. "
            f"Note how it relates to prior arguments. Next: {next_speaker_name}."
        )

    # The rich-reasoning summary is captured in the distill phase
    # (moderator-only turn) — process_response here is never called for
    # the moderator, whose summaries bypass response processing.
