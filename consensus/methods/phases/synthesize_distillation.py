"""Reveal & Synthesis phase handler for Recursive Self-Distillation.

Moderator-only phase. The actual synthesis work is driven by
``get_conclusion_prompt`` on the main method class — this handler
just provides minimal scaffolding.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..base import Phase
from ..phase_handler import PhaseHandler

if TYPE_CHECKING:
    from ...models import Discussion, Entity


class SynthesizeDistillationHandler(PhaseHandler):
    """Phase 4: Reveal original reasoning and compare with validity scores."""

    phase = Phase(
        name="sd_synthesize",
        display_name="Reveal & Synthesis",
        description=(
            "The moderator reveals the original rich reasoning alongside "
            "the blind validity scores, identifying where persuasiveness "
            "substituted for logical validity."
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
