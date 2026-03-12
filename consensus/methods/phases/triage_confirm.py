"""Confirm phase handler for Guided Triage.

All participants review the recommended methods and confirm or
suggest alternatives. The moderator makes the final selection.
"""

from __future__ import annotations

import re
import logging
from typing import TYPE_CHECKING

from ..base import Phase, ProcessedResponse
from ..phase_handler import PhaseHandler

if TYPE_CHECKING:
    from ...models import Discussion, Entity

logger = logging.getLogger(__name__)


class TriageConfirmHandler(PhaseHandler):
    """Phase 3: Group confirms method selection."""

    phase = Phase(
        name="confirm",
        display_name="Method Confirmation",
        description=(
            "All participants review the recommended methods and "
            "confirm or suggest alternatives."
        ),
        rounds=1,
        allow_tools=False,
    )

    def get_system_prompt(self, entity: Entity,
                          discussion: Discussion) -> str:
        state = discussion.method_state
        recs = state.get("recommendations", [])
        rec_text = "\n".join(
            f"- **{r['display_name']}** (`{r['method_name']}`) — "
            f"confidence {r['confidence']:.0%}: {r['reasoning']}"
            for r in recs
        ) if recs else "(no recommendations available)"

        recommended = state.get("recommended_method", "unknown")

        return (
            f"You are {entity.name} participating in a methodology "
            f"selection process.\n"
            f"Topic: {discussion.topic}\n\n"
            f"The moderator recommends: **{recommended}**\n\n"
            f"All recommendations:\n{rec_text}\n\n"
            "Review the recommendation. You may agree, object with "
            "reasoning, or suggest an alternative method."
        )

    def get_turn_prompt(self, entity: Entity,
                        discussion: Discussion) -> str:
        state = discussion.method_state
        recs = state.get("recommendations", [])
        rec_text = "\n".join(
            f"  {i+1}. **{r['display_name']}** (`{r['method_name']}`) — "
            f"{r['reasoning']}"
            for i, r in enumerate(recs)
        ) if recs else "  (no recommendations)"
        recommended = state.get("recommended_method", "unknown")

        if entity.id == discussion.moderator_id:
            return (
                "Review the participants' feedback on the method "
                "recommendation. Make the final selection.\n\n"
                "If a human participant explicitly requested a "
                "different method, honor that request.\n\n"
                "State your final choice clearly using the method's "
                f"registry name (e.g., `{recommended}`)."
            )

        return (
            f"The recommended discussion methods are:\n{rec_text}\n\n"
            f"Top recommendation: `{recommended}`\n\n"
            "Do you agree with this recommendation, or would you "
            "prefer a different method? If you disagree, explain why "
            "and suggest an alternative."
        )

    def process_response(self, content: str, entity: Entity,
                         discussion: Discussion) -> ProcessedResponse:
        """Extract chosen method from moderator's final selection."""
        state = discussion.method_state

        # Only the moderator's response sets the chosen method
        if entity.id != discussion.moderator_id:
            return ProcessedResponse(display_content=content)

        # Try to extract a backtick-quoted method name
        recs = state.get("recommendations", [])
        valid_names = {r["method_name"] for r in recs}

        chosen = None
        # Pattern: `method_name` in backticks
        backtick_matches = re.findall(r'`(\w+)`', content)
        for match in backtick_matches:
            if match in valid_names:
                chosen = match
                break

        # Fallback: check if any recommended method name appears in text
        if not chosen:
            for name in valid_names:
                if name in content.lower():
                    chosen = name
                    break

        # Final fallback: use the recommended method
        if not chosen:
            chosen = state.get("recommended_method")
            logger.info("Could not parse chosen method, falling back to recommended: %s", chosen)

        state["chosen_method"] = chosen
        return ProcessedResponse(display_content=content)
