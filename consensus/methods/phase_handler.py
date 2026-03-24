"""PhaseHandler — the building block for composable discussion phases.

Each PhaseHandler encapsulates all behavior for one phase of a discussion
method: prompts, response processing, advancement logic, and state
initialization.  Methods assemble ordered sequences of handlers.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, ClassVar

from .base import Phase, ProcessedResponse

if TYPE_CHECKING:
    from ..models import Discussion, Entity


class PhaseHandler(ABC):
    """A reusable, self-contained phase of a discussion method.

    Subclasses must set the ``phase`` class attribute and implement
    ``get_system_prompt`` and ``get_turn_prompt``.  All other methods
    have sensible defaults.
    """

    phase: ClassVar[Phase]  # metadata — set as class attribute on each subclass

    # ------------------------------------------------------------------
    # Prompt hooks
    # ------------------------------------------------------------------

    @abstractmethod
    def get_system_prompt(self, entity: Entity,
                          discussion: Discussion) -> str:
        """Return the system prompt for a participant in this phase."""

    @abstractmethod
    def get_turn_prompt(self, entity: Entity,
                        discussion: Discussion) -> str:
        """Return the turn instruction for a participant in this phase."""

    def get_summary_prompt(self, discussion: Discussion,
                           speaker_name: str,
                           next_speaker_name: str) -> str:
        """Return the moderator summary prompt for this phase.

        Default returns empty string (use standard DB template).
        """
        return ""

    def filter_context_message(self, entity_name: str, content: str,
                               role: str,
                               discussion: Discussion, *,
                               current_entity_id: int | None = None) -> str:
        """Transform a context message before sending to the AI.

        Args:
            entity_name: The speaker's name in the original message.
            content: The formatted message content.
            role: The OpenAI message role.
            discussion: The current discussion.
            current_entity_id: The entity being prompted (for selective filtering).

        Default: no transformation.
        """
        return content

    # ------------------------------------------------------------------
    # Response processing
    # ------------------------------------------------------------------

    def process_response(self, content: str, entity: Entity,
                         discussion: Discussion) -> ProcessedResponse:
        """Post-process a participant's response in this phase.

        Default: no transformation, no extracted data.
        """
        return ProcessedResponse(display_content=content)

    # ------------------------------------------------------------------
    # Phase lifecycle
    # ------------------------------------------------------------------

    def init_state(self, discussion: Discussion) -> dict:
        """Return phase-specific initial state keys.

        These are merged into the discussion's method_state at
        discussion start.  Default: no additional state.
        """
        return {}

    def should_advance(self, discussion: Discussion) -> bool:
        """Return True if this phase is complete.

        Default: advance when phase_round exceeds self.phase.rounds.
        Phases with rounds=0 never auto-advance.
        """
        if self.phase.rounds == 0:
            return False
        phase_round = discussion.method_state.get("phase_round", 1)
        return phase_round > self.phase.rounds

    def get_transition_message(self, discussion: Discussion) -> str:
        """Return a system message posted when transitioning TO this phase."""
        return (
            f"**Phase transition:** Moving to *{self.phase.display_name}*."
            f"\n\n{self.phase.description}"
        )

    # ------------------------------------------------------------------
    # Round lifecycle
    # ------------------------------------------------------------------

    def on_round_complete(self, discussion: Discussion) -> None:
        """Called after ``phase_round`` is incremented by the method.

        Handlers can override to update internal sub-state (e.g. huddle
        round progression).  Default: no-op.
        """

    # ------------------------------------------------------------------
    # Turn order
    # ------------------------------------------------------------------

    def get_turn_order(self, entity_ids: list[int],
                       discussion: Discussion) -> list[int]:
        """Return entity IDs in the desired order for this phase.

        Default: preserve existing order.
        """
        return entity_ids
