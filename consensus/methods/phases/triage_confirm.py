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

#: Shown in place of the recommendation list when the classifier failed, so
#: the fallback method is never presented as a considered choice (#72).
_RECOMMENDER_FAILURE_LINE = (
    "  (!) The automatic method recommendation FAILED: {detail}\n"
    "  No method was actually recommended — the entry below is a fallback."
)


def _recommendation_block(state: dict, lines: str) -> str:
    """Prefix a rendered recommendation list with any classifier failure."""
    detail = state.get("recommender_error")
    if not detail:
        return lines
    return _RECOMMENDER_FAILURE_LINE.format(detail=detail) + "\n" + lines


def selectable_method_names() -> set[str]:
    """Return every method the group may choose in the confirm phase.

    The whole registry minus the non-recommendable meta-methods: choosing
    ``triage`` here would route the group into the blocked-switch recovery
    dialog rather than starting a discussion.  Shared by the prompt and
    ``process_response`` so the list the moderator is shown is exactly the
    list that will be accepted (issue #72).
    """
    from .. import list_methods
    from ..recommender import _EXCLUDED_METHODS

    return {
        m["name"] for m in list_methods()
        if m["name"] not in _EXCLUDED_METHODS
    }


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
        """Describe the confirm phase and the shortlist under review."""
        state = discussion.method_state
        recs = state.get("recommendations", [])
        rec_text = "\n".join(
            f"- **{r['display_name']}** (`{r['method_name']}`) — "
            f"confidence {r['confidence']:.0%}: {r['reasoning']}"
            for r in recs
        ) if recs else "(no recommendations available)"
        rec_text = _recommendation_block(state, rec_text)

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
        """Ask this entity to confirm or override the recommendation.

        The moderator gets the deciding prompt; everyone else is asked for
        feedback on the shortlist.
        """
        state = discussion.method_state
        recs = state.get("recommendations", [])
        rec_text = "\n".join(
            f"  {i+1}. **{r['display_name']}** (`{r['method_name']}`) — "
            f"{r['reasoning']}"
            for i, r in enumerate(recs)
        ) if recs else "  (no recommendations)"
        rec_text = _recommendation_block(state, rec_text)
        recommended = state.get("recommended_method", "unknown")

        if entity.id == discussion.moderator_id:
            # The moderator makes the final selection, so it must know the
            # "recommendation" it is about to rubber-stamp is a fallback —
            # and, since there is then no shortlist, which names it may
            # actually pick from.  Asking it to "name the method you want"
            # without showing the candidates is not an actionable
            # instruction (issue #72).
            failure = ""
            if state.get("recommender_error"):
                candidates = ", ".join(
                    f"`{n}`" for n in sorted(selectable_method_names()))
                failure = (
                    f"{rec_text}\n\n"
                    f"Choose from: {candidates}\n\n"
                )
            return (
                f"{failure}"
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
        if not valid_names:
            # No shortlist to validate against — whatever the reason (the
            # classifier failed, or it was never run).  The failure notice
            # asks the group to name the method they want, and that has to
            # be actionable (issue #72), so widen to the registry rather
            # than accepting anything.
            valid_names = selectable_method_names()

        chosen = None
        # Pattern: `method_name` in backticks
        backtick_matches = re.findall(r'`(\w+)`', content)
        for match in backtick_matches:
            if match in valid_names:
                chosen = match
                break

        # Fallback: check if any recommended method name appears in the
        # text as a whole word.  Substring matching would pick "ach" out
        # of "approach"; iterate deterministically by recommendation
        # confidence order rather than set order.
        if not chosen:
            for rec in recs:
                name = rec["method_name"]
                if re.search(r"\b" + re.escape(name) + r"\b",
                             content, re.IGNORECASE):
                    chosen = name
                    break

        # Final fallback: use the recommended method
        if not chosen:
            chosen = state.get("recommended_method")
            logger.info("Could not parse chosen method, falling back to recommended: %s", chosen)

        state["chosen_method"] = chosen
        return ProcessedResponse(display_content=content)
