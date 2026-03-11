"""Frame Premortem phase handler for Premortem Analysis.

The moderator states a preliminary conclusion or plan that will be
subjected to premortem analysis.  The handler captures the conclusion
from the moderator's framing message.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..base import Phase, ProcessedResponse
from ..phase_handler import PhaseHandler

if TYPE_CHECKING:
    from ...models import Discussion, Entity


class FramePremortemHandler(PhaseHandler):
    """Phase 1: Frame the conclusion/plan to be analysed."""

    phase = Phase(
        name="frame",
        display_name="Framing",
        description=(
            "The moderator states a preliminary conclusion or plan "
            "that will be subjected to premortem analysis."
        ),
        rounds=1,
    )

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    def init_state(self, discussion: Discussion) -> dict:
        return {"conclusion": ""}

    # ------------------------------------------------------------------
    # Prompts
    # ------------------------------------------------------------------

    def get_system_prompt(self, entity: Entity,
                          discussion: Discussion) -> str:
        return ""  # moderator handles framing

    def get_turn_prompt(self, entity: Entity,
                        discussion: Discussion) -> str:
        return ""

    # ------------------------------------------------------------------
    # Response processing
    # ------------------------------------------------------------------

    def process_response(self, content: str, entity: Entity,
                         discussion: Discussion) -> ProcessedResponse:
        state = discussion.method_state
        if not state.get("conclusion"):
            state["conclusion"] = content.strip()
        return ProcessedResponse(display_content=content)

    # ------------------------------------------------------------------
    # Phase advancement
    # ------------------------------------------------------------------

    def should_advance(self, discussion: Discussion) -> bool:
        return bool(discussion.method_state.get("conclusion"))
