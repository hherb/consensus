"""Base class and data structures for discussion methods.

A DiscussionMethod controls the high-level structure of a discussion:
which phases it goes through, what prompts are used in each phase,
how responses are post-processed, and when to advance between phases.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from ..models import Discussion, Entity

logger = logging.getLogger(__name__)


@dataclass
class Phase:
    """A named stage within a discussion method."""

    name: str  # machine name, e.g. "hypothesize"
    display_name: str  # human-readable, e.g. "Hypothesis Generation"
    description: str = ""  # shown to participants
    rounds: int = 1  # how many full rounds in this phase (0 = until condition)
    allow_tools: bool = True  # whether tool use is enabled


@dataclass
class ProcessedResponse:
    """Result of post-processing a participant's raw response.

    Methods can extract structured data (e.g. belief distributions,
    hypothesis ratings) from the natural-language response and store
    it in ``extracted_data``.  The ``display_content`` may differ from
    the original response (e.g. with appended visualisations).
    """

    display_content: str  # what gets stored as the message content
    extracted_data: dict = field(default_factory=dict)
    phase_complete: bool = False  # hint that this phase should end


class DiscussionMethod(ABC):
    """Abstract base for all discussion methods.

    Subclasses must set class-level ``name``, ``display_name``,
    ``description``, and ``default_phases``, and implement the
    abstract prompt/processing hooks.
    """

    # -- Class-level metadata (override in subclasses) --
    name: str = ""
    display_name: str = ""
    description: str = ""
    default_phases: list[Phase] = []

    # ------------------------------------------------------------------
    # Phase management
    # ------------------------------------------------------------------

    def init_state(self, discussion: Discussion) -> dict:
        """Return the initial method_state for a new discussion.

        Called once when a discussion starts.  Subclasses should
        populate hypothesis lists, belief priors, etc.
        """
        return {
            "current_phase": self.default_phases[0].name if self.default_phases else "",
            "phase_round": 1,
        }

    def current_phase(self, discussion: Discussion) -> Optional[Phase]:
        """Return the Phase object for the discussion's current phase."""
        phase_name = discussion.method_state.get("current_phase", "")
        for p in self.default_phases:
            if p.name == phase_name:
                return p
        return self.default_phases[0] if self.default_phases else None

    def should_advance_phase(self, discussion: Discussion) -> bool:
        """Return ``True`` if the current phase is complete.

        Default: advance when ``phase_round`` exceeds the phase's
        ``rounds`` setting.  Override for condition-based transitions.
        """
        phase = self.current_phase(discussion)
        if not phase or phase.rounds == 0:
            return False
        phase_round = discussion.method_state.get("phase_round", 1)
        return phase_round > phase.rounds

    def advance_phase(self, discussion: Discussion) -> Optional[Phase]:
        """Move to the next phase.  Returns the new Phase, or None if done."""
        phases = self.default_phases
        current = self.current_phase(discussion)
        if not current:
            return None
        try:
            idx = next(i for i, p in enumerate(phases) if p.name == current.name)
        except StopIteration:
            return None
        if idx + 1 >= len(phases):
            return None  # all phases exhausted
        next_phase = phases[idx + 1]
        discussion.method_state["current_phase"] = next_phase.name
        discussion.method_state["phase_round"] = 1
        return next_phase

    # ------------------------------------------------------------------
    # Prompt hooks — called by moderator.py
    # ------------------------------------------------------------------

    @abstractmethod
    def get_system_prompt(self, entity: Entity, discussion: Discussion) -> str:
        """Return the system prompt for an entity in the current phase."""

    @abstractmethod
    def get_turn_prompt(self, entity: Entity, discussion: Discussion) -> str:
        """Return the turn-specific instruction for the entity."""

    def get_summary_prompt(self, discussion: Discussion,
                           speaker_name: str,
                           next_speaker_name: str) -> str:
        """Return the moderator summary prompt for the current phase.

        Default returns empty string (use standard summary logic).
        """
        return ""

    def get_conclusion_prompt(self, discussion: Discussion) -> str:
        """Return the final conclusion prompt.

        Default returns empty string (use standard conclusion logic).
        """
        return ""

    def get_phase_transition_message(self, new_phase: Phase,
                                     discussion: Discussion) -> str:
        """Return a system message announcing a phase transition."""
        return (
            f"**Phase transition:** Moving to *{new_phase.display_name}*.\n\n"
            f"{new_phase.description}"
        )

    # ------------------------------------------------------------------
    # Response post-processing
    # ------------------------------------------------------------------

    def process_response(self, content: str, entity: Entity,
                         discussion: Discussion) -> ProcessedResponse:
        """Post-process a participant's response.

        Default: no transformation, no extracted data.
        Override to parse structured output (beliefs, ratings, etc.).
        """
        return ProcessedResponse(display_content=content)

    # ------------------------------------------------------------------
    # Round lifecycle hooks
    # ------------------------------------------------------------------

    def on_round_complete(self, discussion: Discussion) -> None:
        """Called when a full round (all participants) completes.

        Subclasses can override to update method-specific counters
        (e.g. diffusion round tracking).  The base implementation
        increments ``phase_round``.
        """
        discussion.method_state["phase_round"] = (
            discussion.method_state.get("phase_round", 1) + 1
        )

    # ------------------------------------------------------------------
    # Turn order
    # ------------------------------------------------------------------

    def get_turn_order(self, entity_ids: list[int],
                       discussion: Discussion) -> list[int]:
        """Return entity IDs in the desired turn order for the current phase.

        Default: preserve existing order.
        """
        return entity_ids

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise method metadata for the frontend."""
        return {
            "name": self.name,
            "display_name": self.display_name,
            "description": self.description,
            "phases": [
                {"name": p.name, "display_name": p.display_name,
                 "description": p.description, "rounds": p.rounds}
                for p in self.default_phases
            ],
        }
