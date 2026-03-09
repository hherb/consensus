"""Open Discussion — the default, unstructured discussion method.

Wraps the original round-robin behavior as a DiscussionMethod so that
existing discussions continue to work unchanged.  This method has a
single implicit phase and delegates all prompt logic to the standard
prompt templates in the database.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import DiscussionMethod, Phase

if TYPE_CHECKING:
    from ..models import Discussion, Entity


class OpenDiscussion(DiscussionMethod):
    """Traditional moderated round-robin discussion."""

    name = "open_discussion"
    display_name = "Open Discussion"
    description = (
        "Traditional moderated round-robin discussion.  Participants take "
        "turns in order, with the moderator summarising after each turn.  "
        "An optional devil's advocate provides critical analysis."
    )
    default_phases = [
        Phase(
            name="discussion",
            display_name="Discussion",
            description="Open-ended round-robin discussion.",
            rounds=0,  # unlimited — controlled by max_rounds
        ),
    ]

    def get_system_prompt(self, entity: Entity, discussion: Discussion) -> str:
        """Return empty string — use standard prompt templates."""
        return ""

    def get_turn_prompt(self, entity: Entity, discussion: Discussion) -> str:
        """Return empty string — use standard prompt templates."""
        return ""
