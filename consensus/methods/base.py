"""Base class and data structures for discussion methods.

A DiscussionMethod controls the high-level structure of a discussion:
which phases it goes through, what prompts are used in each phase,
how responses are post-processed, and when to advance between phases.

Methods can either override hooks directly (traditional approach) or
set ``phase_handlers`` to delegate hooks to composable PhaseHandler
instances.  The two approaches are fully compatible: subclass overrides
always take precedence over handler delegation via normal Python MRO.
"""

from __future__ import annotations

import logging
from abc import ABC
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from ..models import Discussion, Entity
    from .phase_handler import PhaseHandler as _PhaseHandler

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

    Subclasses must set class-level ``name``, ``display_name``, and
    ``description``.  They can either:

    1. Override prompt/processing hooks directly (traditional), or
    2. Set ``phase_handlers`` to a tuple of PhaseHandler instances
       to delegate hooks automatically.

    When ``phase_handlers`` is set and ``default_phases`` is not
    explicitly defined, ``default_phases`` is auto-derived from the
    handlers' ``phase`` attributes.
    """

    # -- Class-level metadata (override in subclasses) --
    name: str = ""
    display_name: str = ""
    description: str = ""
    default_phases: tuple[Phase, ...] = ()
    phase_handlers: tuple[_PhaseHandler, ...] = ()

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        # Auto-derive default_phases from phase_handlers if handlers
        # are set and default_phases was NOT explicitly defined in this
        # subclass (i.e. it still inherits the base empty tuple or a
        # parent's value).
        if "phase_handlers" in cls.__dict__ and "default_phases" not in cls.__dict__:
            cls.default_phases = tuple(h.phase for h in cls.phase_handlers)

    # ------------------------------------------------------------------
    # Handler lookup helpers
    # ------------------------------------------------------------------

    def _handler_for_phase(self, phase_name: str) -> Optional[_PhaseHandler]:
        """Return the PhaseHandler for the given phase name, or None."""
        for h in self.phase_handlers:
            if h.phase.name == phase_name:
                return h
        return None

    def _active_handler(self, discussion: Discussion) -> Optional[_PhaseHandler]:
        """Return the PhaseHandler for the discussion's current phase."""
        phase_name = discussion.method_state.get("current_phase", "")
        return self._handler_for_phase(phase_name)

    # ------------------------------------------------------------------
    # Phase management
    # ------------------------------------------------------------------

    def init_state(self, discussion: Discussion) -> dict:
        """Return the initial method_state for a new discussion.

        Called once when a discussion starts.  If ``phase_handlers`` is
        set, merges each handler's ``init_state`` into the base state.
        Subclasses can override to populate hypothesis lists, etc.
        """
        state = {
            "current_phase": self.default_phases[0].name if self.default_phases else "",
            "phase_round": 1,
        }
        for handler in self.phase_handlers:
            state.update(handler.init_state(discussion))
        return state

    def current_phase(self, discussion: Discussion) -> Optional[Phase]:
        """Return the Phase object for the discussion's current phase."""
        phase_name = discussion.method_state.get("current_phase", "")
        for p in self.default_phases:
            if p.name == phase_name:
                return p
        return self.default_phases[0] if self.default_phases else None

    def should_advance_phase(self, discussion: Discussion) -> bool:
        """Return ``True`` if the current phase is complete.

        Delegates to the active handler's ``should_advance`` if present.
        Default: advance when ``phase_round`` exceeds the phase's
        ``rounds`` setting.  Override for condition-based transitions.
        """
        handler = self._active_handler(discussion)
        if handler is not None:
            return handler.should_advance(discussion)
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

    def get_system_prompt(self, entity: Entity, discussion: Discussion) -> str:
        """Return the system prompt for an entity in the current phase.

        Delegates to the active handler if present, otherwise returns "".
        """
        handler = self._active_handler(discussion)
        if handler is not None:
            return handler.get_system_prompt(entity, discussion)
        return ""

    def get_turn_prompt(self, entity: Entity, discussion: Discussion) -> str:
        """Return the turn-specific instruction for the entity.

        Delegates to the active handler if present, otherwise returns "".
        """
        handler = self._active_handler(discussion)
        if handler is not None:
            return handler.get_turn_prompt(entity, discussion)
        return ""

    def get_summary_prompt(self, discussion: Discussion,
                           speaker_name: str,
                           next_speaker_name: str) -> str:
        """Return the moderator summary prompt for the current phase.

        Delegates to the active handler if present.
        Default returns empty string (use standard summary logic).
        """
        handler = self._active_handler(discussion)
        if handler is not None:
            return handler.get_summary_prompt(discussion, speaker_name,
                                              next_speaker_name)
        return ""

    def filter_context_message(self, entity_name: str, content: str,
                               role: str, discussion: Discussion) -> str:
        """Transform a context message before it's sent to the AI.

        Called by the moderator when building the message context for both
        participant turns and moderator summaries.  Methods can use this
        to anonymise speaker names, redact content, etc.

        Delegates to the active handler if present.

        Args:
            entity_name: The speaker's name in the original message.
            content: The formatted message content (may include speaker prefix).
            role: The OpenAI message role ("user", "assistant", "system").
            discussion: The current discussion.

        Returns:
            The (possibly transformed) content string.
        """
        handler = self._active_handler(discussion)
        if handler is not None:
            return handler.filter_context_message(entity_name, content,
                                                  role, discussion)
        return content

    def get_conclusion_prompt(self, discussion: Discussion) -> str:
        """Return the final conclusion prompt.

        Default returns empty string (use standard conclusion logic).
        """
        return ""

    def get_phase_transition_message(self, new_phase: Phase,
                                     discussion: Discussion) -> str:
        """Return a system message announcing a phase transition.

        Delegates to the handler for the *new* phase if present.
        """
        handler = self._handler_for_phase(new_phase.name)
        if handler is not None:
            return handler.get_transition_message(discussion)
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

        Delegates to the active handler if present.
        Default: no transformation, no extracted data.
        Override to parse structured output (beliefs, ratings, etc.).
        """
        handler = self._active_handler(discussion)
        if handler is not None:
            return handler.process_response(content, entity, discussion)
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

        Delegates to the active handler if present.
        Default: preserve existing order.
        """
        handler = self._active_handler(discussion)
        if handler is not None:
            return handler.get_turn_order(entity_ids, discussion)
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
