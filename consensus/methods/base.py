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
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from ..models import Discussion, Entity
    from .phase_handler import PhaseHandler as _PhaseHandler

logger = logging.getLogger(__name__)

#: Sentinel returned by ``PhaseHandler.next_phase`` to request the
#: default linear phase progression.  A dunder name so it can never
#: collide with a real phase name.
LINEAR_NEXT: str = "__linear__"

#: Default loop-guard budget: a method may enter phases at most
#: ``len(default_phases) * MAX_PHASE_VISITS_PER_PHASE`` times unless it
#: sets ``max_phase_entries`` explicitly.  The budget caps *total*
#: transitions (linear ones included), not visits per individual phase
#: — a method that loops heavily draws from the same budget as its
#: linear tail.  Linear methods make at most ``len(default_phases) - 1``
#: transitions, so they can never hit this.
MAX_PHASE_VISITS_PER_PHASE: int = 5


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

    ``display_content`` may differ from the original response (e.g. with
    appended visualisations) and is what gets stored as the message
    content.  Handlers that extract structured data (belief
    distributions, hypothesis ratings, ...) must write it into
    ``discussion.method_state`` themselves — the flow layer consumes
    nothing else from this object (issue #21).
    """

    display_content: str  # what gets stored as the message content


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
    #: Loop guard: maximum total phase entries (transitions) before the
    #: method is forcibly completed.  0 = auto (``len(default_phases) *
    #: MAX_PHASE_VISITS_PER_PHASE``).  Only relevant for methods whose
    #: ``next_phase`` hook can revisit phases.
    max_phase_entries: int = 0

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
        state: dict[str, Any] = {
            "current_phase": self.default_phases[0].name if self.default_phases else "",
            "phase_round": 1,
        }
        for handler in self.phase_handlers:
            handler_state = handler.init_state(discussion)
            for key in handler_state:
                if key in state:
                    logger.warning(
                        "Handler %s.init_state key %r overwrites existing "
                        "state from a previous handler",
                        type(handler).__name__, key,
                    )
            state.update(handler_state)
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

    def next_phase(self, discussion: Discussion) -> Optional[str]:
        """Return the name of the phase to enter next, or None to finish.

        The active handler is consulted first: it may return a phase
        name (jump — enables loops), ``None`` (end the method early,
        e.g. abort after an unrecoverable phase failure), or the
        ``LINEAR_NEXT`` sentinel to defer to the default linear order.

        Method subclasses can override this directly; calling
        ``super().next_phase(discussion)`` yields the linear default
        (returning ``LINEAR_NEXT`` from an override works too).

        Note: any ``method_state`` mutations made inside this hook are
        committed even if ``advance_phase`` then rejects the transition
        (unknown phase name or loop-guard trip) — the method ends, but
        the mutated state is what gets persisted.
        """
        handler = self._active_handler(discussion)
        if handler is not None:
            choice = handler.next_phase(discussion)
            if choice != LINEAR_NEXT:
                return choice
        return self._linear_next(discussion)

    def _linear_next(self, discussion: Discussion) -> Optional[str]:
        """Return the successor of the current phase in ``default_phases``."""
        phases = self.default_phases
        current = self.current_phase(discussion)
        if not current:
            return None
        idx = next((i for i, p in enumerate(phases)
                    if p.name == current.name), None)
        if idx is None or idx + 1 >= len(phases):
            return None  # all phases exhausted
        return phases[idx + 1].name

    def advance_phase(self, discussion: Discussion) -> Optional[Phase]:
        """Move to the phase chosen by ``next_phase``.

        Returns the new Phase, or None if the method is done — either
        because ``next_phase`` returned None/an unknown name, or because
        the loop guard was exhausted.
        """
        target = self.next_phase(discussion)
        if target == LINEAR_NEXT:
            # A method-level override returned the sentinel instead of
            # calling super().next_phase() — honor the intent.
            target = self._linear_next(discussion)
        if target is None:
            return None
        phase = next((p for p in self.default_phases if p.name == target),
                     None)
        if phase is None:
            logger.warning(
                "Method %s next_phase returned unknown phase %r — "
                "ending method", self.name, target,
            )
            return None
        entries = discussion.method_state.get("_phase_entries", 0) + 1
        cap = (self.max_phase_entries
               or len(self.default_phases) * MAX_PHASE_VISITS_PER_PHASE)
        if entries > cap:
            logger.warning(
                "Method %s exceeded the loop guard of %d phase entries — "
                "ending method", self.name, cap,
            )
            return None
        discussion.method_state["_phase_entries"] = entries
        discussion.method_state["current_phase"] = phase.name
        discussion.method_state["phase_round"] = 1
        return phase

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
                               role: str, discussion: Discussion, *,
                               current_entity_id: int | None = None) -> str:
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
            current_entity_id: The entity being prompted (for selective filtering).

        Returns:
            The (possibly transformed) content string.
        """
        handler = self._active_handler(discussion)
        if handler is not None:
            return handler.filter_context_message(
                entity_name, content, role, discussion,
                current_entity_id=current_entity_id)
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

    def get_method_complete_message(self, discussion: Discussion) -> str:
        """Return a system message announcing the end of the method.

        Called by the flow when ``advance_phase`` returns ``None`` —
        both normal completion and an early abort.  Delegates to the
        handler of the phase the method ended in.  Default: empty
        string (no message posted).
        """
        handler = self._active_handler(discussion)
        if handler is not None:
            return handler.get_method_complete_message(discussion)
        return ""

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
        increments ``phase_round`` and delegates to the active handler.
        """
        discussion.method_state["phase_round"] = (
            discussion.method_state.get("phase_round", 1) + 1
        )
        handler = self._active_handler(discussion)
        if handler is not None:
            handler.on_round_complete(discussion)

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
