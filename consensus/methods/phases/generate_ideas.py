"""Silent idea generation phase handler for Nominal Group Technique.

Each participant independently proposes candidate ideas via the forced
``submit_ideas`` output tool (issue #23); free-text numbered-list
parsing remains the human/fallback path.  Context is anonymised
(Delphi-style) so ideas are judged on content, not authorship.  If no
ideas at all are collected after ``MAX_GENERATE_ROUNDS``, the method
aborts early — every later phase needs an idea list.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ..base import LINEAR_NEXT, OutputToolSpec, Phase, ProcessedResponse
from ..parsing import parse_numbered_list
from ..phase_handler import PhaseHandler
from ._delphi_helpers import anonymise_content
from ._ngt_helpers import (
    IDEAS_TOOL_PARAMETERS,
    MAX_GENERATE_ROUNDS,
    MIN_IDEA_LENGTH,
    record_ideas,
    validate_ideas_payload,
)

if TYPE_CHECKING:
    from ...models import Discussion, Entity

logger = logging.getLogger(__name__)


class GenerateIdeasHandler(PhaseHandler):
    """Phase 1: Silent independent idea generation."""

    phase = Phase(
        name="generate",
        display_name="Silent Idea Generation",
        description=(
            "Each participant independently proposes candidate ideas "
            "or solutions.  Contributions are anonymised — ideas are "
            "judged on content, not authorship."
        ),
        rounds=1,
    )

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    def init_state(self, discussion: Discussion) -> dict:
        return {"ideas": []}

    # ------------------------------------------------------------------
    # Prompts
    # ------------------------------------------------------------------

    def get_system_prompt(self, entity: Entity,
                          discussion: Discussion) -> str:
        return (
            f"You are {entity.name}, participating in a Nominal Group "
            "Technique (NGT) brainstorming session.\n"
            f"Topic: {discussion.topic}\n\n"
            "SILENT IDEA GENERATION PHASE\n\n"
            "Independently propose 3-7 distinct candidate ideas or "
            "solutions for this topic.  IMPORTANT: Do not react to or "
            "build on others' contributions — this is your independent "
            "thinking.  Diversity beats polish; include unconventional "
            "ideas.\n\n"
            "Submit your ideas by calling the submit_ideas tool with an "
            "array of idea strings — each a complete, specific, "
            "self-contained proposal — plus a brief rationale in the "
            "'reasoning' field."
        )

    def get_turn_prompt(self, entity: Entity,
                        discussion: Discussion) -> str:
        return (
            f"It is your turn, {entity.name}.  Independently propose "
            "3-7 distinct ideas by calling the submit_ideas tool."
        )

    def get_summary_prompt(self, discussion: Discussion,
                           speaker_name: str,
                           next_speaker_name: str) -> str:
        return (
            "A set of ideas has been received.  Do NOT reveal, quote, "
            "or evaluate any of them — silent generation requires that "
            "participants do not anchor on each other.  Simply invite "
            f"the next participant.\n\n{next_speaker_name}, please "
            "independently propose your candidate ideas on the topic."
        )

    # ------------------------------------------------------------------
    # Context filtering — anonymise authorship
    # ------------------------------------------------------------------

    def filter_context_message(self, entity_name: str, content: str,
                               role: str,
                               discussion: Discussion, *,
                               current_entity_id: int | None = None) -> str:
        return anonymise_content(content, discussion)

    # ------------------------------------------------------------------
    # Response processing (free-text / human fallback path)
    # ------------------------------------------------------------------

    def process_response(self, content: str, entity: Entity,
                         discussion: Discussion) -> ProcessedResponse:
        state = discussion.method_state
        items = parse_numbered_list(content, min_length=MIN_IDEA_LENGTH)
        if items:
            record_ideas(state, entity, items)
        else:
            logger.warning(
                "Could not extract ideas from %s's response", entity.name)
        return ProcessedResponse(display_content=content)

    # ------------------------------------------------------------------
    # Structured output (issue #23)
    # ------------------------------------------------------------------

    requires_structured_output = True

    def get_output_tool(self, entity: Entity,
                        discussion: Discussion) -> OutputToolSpec:
        return OutputToolSpec(
            name="submit_ideas",
            description=("Submit your independent candidate ideas as an "
                         "array of complete proposal strings, plus your "
                         "reasoning."),
            parameters=IDEAS_TOOL_PARAMETERS,
        )

    def validate_output(self, payload: dict, entity: Entity,
                        discussion: Discussion) -> str:
        return validate_ideas_payload(payload)

    def process_structured_response(self, payload: dict, entity: Entity,
                                    discussion: Discussion) -> ProcessedResponse:
        state = discussion.method_state
        texts = [str(i).strip() for i in payload["ideas"] if str(i).strip()]
        accepted = record_ideas(state, entity, texts)
        reasoning = str(payload.get("reasoning") or "").strip()
        numbered = "\n".join(f"{n}. {idea['text']}"
                             for n, idea in enumerate(accepted, 1))
        display = f"{reasoning}\n\n{numbered}" if numbered else reasoning
        return ProcessedResponse(display_content=display)

    # ------------------------------------------------------------------
    # Phase advancement
    # ------------------------------------------------------------------

    def should_advance(self, discussion: Discussion) -> bool:
        state = discussion.method_state
        phase_round = state.get("phase_round", 1)
        if phase_round > MAX_GENERATE_ROUNDS:
            logger.warning(
                "Idea generation reached round %d; advancing with %d "
                "idea(s) collected.",
                phase_round, len(state.get("ideas", [])),
            )
            return True
        return bool(state.get("ideas")) and phase_round > 1

    def _gave_up(self, discussion: Discussion) -> bool:
        """True if generation exhausted its rounds without any ideas."""
        state = discussion.method_state
        return (not state.get("ideas")
                and state.get("phase_round", 1) > MAX_GENERATE_ROUNDS)

    def next_phase(self, discussion: Discussion) -> str | None:
        """Abort the method when generation produced nothing.

        Without ideas the remaining phases (cluster/clarify/allocate/
        rank) are degenerate — they would consolidate and vote over an
        empty list and burn API spend producing nothing usable.
        """
        if self._gave_up(discussion):
            logger.warning(
                "Idea generation produced no ideas — ending the NGT "
                "method early")
            return None
        return LINEAR_NEXT

    def get_method_complete_message(self, discussion: Discussion) -> str:
        if not self._gave_up(discussion):
            return ""
        return (
            "⚠️ **Nominal Group Technique ended early.** The generation "
            f"phase collected no usable ideas after {MAX_GENERATE_ROUNDS} "
            "rounds, so the clustering, clarification, and voting phases "
            "were skipped.  Consider rephrasing the topic as an open "
            "'How might we…' question and starting a new discussion."
        )
