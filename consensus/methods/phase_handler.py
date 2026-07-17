"""PhaseHandler — the building block for composable discussion phases.

Each PhaseHandler encapsulates all behavior for one phase of a discussion
method: prompts, response processing, advancement logic, and state
initialization.  Methods assemble ordered sequences of handlers.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, ClassVar

from .base import LINEAR_NEXT, OutputToolSpec, Phase, ProcessedResponse

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
    # Structured output (issue #23)
    # ------------------------------------------------------------------

    #: True when this handler forces structured output via a declared
    #: output tool.  Read at setup time to require tool-capable models
    #: (issue #23) — handlers that set it must return a spec from
    #: ``get_output_tool``.
    requires_structured_output: ClassVar[bool] = False

    def get_output_tool(self, entity: Entity,
                        discussion: Discussion) -> OutputToolSpec | None:
        """Declare the forced output tool for this phase.

        Return an ``OutputToolSpec`` to have AI turns in this phase
        generated through a forced tool call (issue #23), or ``None``
        (default) for ordinary free-text turns.
        """
        return None

    def validate_output(self, payload: dict, entity: Entity,
                        discussion: Discussion) -> str:
        """Semantically validate a structured-output payload.

        Return ``""`` when the payload is acceptable, or a
        human-readable error the model can act on — the turn generator
        feeds it back and retries within the same conversation.
        Default: accept everything.
        """
        return ""

    def process_structured_response(self, payload: dict, entity: Entity,
                                    discussion: Discussion) -> ProcessedResponse:
        """Handle a validated structured-output payload.

        Counterpart of ``process_response`` for the forced-tool path:
        write extracted data into ``discussion.method_state`` and build
        the display content.  Default: render the payload as JSON.
        """
        return ProcessedResponse(
            display_content=json.dumps(payload, indent=2))

    def resolve_input_schema(self, spec: OutputToolSpec, entity: Entity,
                             discussion: Discussion) -> dict:
        """Return the JSON schema to render a human input form / pre-check.

        Default: the tool's declared ``parameters`` unchanged.  Handlers
        whose schema uses runtime-derived keys (e.g. a belief map keyed by
        hypothesis label) override this to expand ``additionalProperties``
        into explicit properties so the frontend can enumerate fields.
        """
        return spec.parameters

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

    def next_phase(self, discussion: Discussion) -> str | None:
        """Choose which phase to enter when this phase completes.

        Return a phase name to jump there (backwards jumps create
        loops), ``None`` to end the method early (abort), or the
        ``LINEAR_NEXT`` sentinel (default) to follow the method's
        linear phase order.  Jumps are bounded by the method's loop
        guard (``max_phase_entries``).

        Note: any ``method_state`` mutations made inside this hook are
        committed even if ``advance_phase`` then rejects the transition
        (unknown phase name or loop-guard trip) — the method ends, but
        the mutated state is what gets persisted.
        """
        return LINEAR_NEXT

    def get_transition_message(self, discussion: Discussion) -> str:
        """Return a system message posted when transitioning TO this phase."""
        return (
            f"**Phase transition:** Moving to *{self.phase.display_name}*."
            f"\n\n{self.phase.description}"
        )

    def get_method_complete_message(self, discussion: Discussion) -> str:
        """Return a system message posted when the method ends in this phase.

        Called for both normal completion (linear order exhausted) and
        early abort (``next_phase`` returned ``None``).  Default: empty
        string (no message posted).  Handlers that can abort the method
        should override this to explain why to the user.
        """
        return ""

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
