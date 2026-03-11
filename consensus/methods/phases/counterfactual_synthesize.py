"""Synthesize phase handler for Counterfactual Stress Testing.

Moderator-only phase that triggers the final conclusion prompt.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..base import Phase
from ..phase_handler import PhaseHandler

if TYPE_CHECKING:
    from ...models import Discussion, Entity


class SynthesizeHandler(PhaseHandler):
    """Phase 4: Moderator synthesizes stress test results."""

    phase = Phase(
        name="synthesize",
        display_name="Synthesis",
        description=(
            "The moderator classifies each claim by structural importance "
            "and assesses the overall robustness of the conclusion."
        ),
        rounds=1,
        allow_tools=False,
    )

    def get_turn_order(self, entity_ids: list[int],
                       discussion: Discussion) -> list[int]:
        return [discussion.moderator_id]

    def get_system_prompt(self, entity: Entity,
                          discussion: Discussion) -> str:
        return ""

    def get_turn_prompt(self, entity: Entity,
                        discussion: Discussion) -> str:
        return ""
