"""Option enumeration phase handler for the Weighted Decision Matrix.

Each participant proposes the decision alternatives via the forced
``submit_options`` output tool (issue #23); free-text numbered-list
parsing remains the human/fallback path.  Unlike NGT's silent
generation, contributions are visible — participants should complement
each other's option lists, not duplicate them.  If no options at all
are collected after ``MAX_OPTIONS_ROUNDS``, the method aborts early —
every later phase needs an option list.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ..base import LINEAR_NEXT, OutputToolSpec, Phase, ProcessedResponse
from ..parsing import parse_numbered_list
from ..phase_handler import PhaseHandler
from ._mcda_helpers import (
    MAX_OPTIONS_ROUNDS,
    MIN_OPTION_LENGTH,
    OPTIONS_TOOL_PARAMETERS,
    record_options,
    validate_options_payload,
)
from ._mcda_analysis import format_options

if TYPE_CHECKING:
    from ...models import Discussion, Entity

logger = logging.getLogger(__name__)


class EnumerateOptionsHandler(PhaseHandler):
    """Phase 1: Enumerate the decision alternatives."""

    phase = Phase(
        name="options",
        display_name="Option Enumeration",
        description=(
            "Participants enumerate the alternatives the decision is "
            "between — options named in the topic plus any missing "
            "alternatives worth considering."
        ),
        rounds=1,
    )

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    def init_state(self, discussion: Discussion) -> dict:
        return {"options": []}

    # ------------------------------------------------------------------
    # Prompts
    # ------------------------------------------------------------------

    def get_system_prompt(self, entity: Entity,
                          discussion: Discussion) -> str:
        options_text = format_options(discussion.method_state)
        return (
            f"You are {entity.name}, participating in a Weighted "
            "Decision Matrix (multi-criteria decision analysis).\n"
            f"Topic: {discussion.topic}\n\n"
            "OPTION ENUMERATION PHASE\n\n"
            "Enumerate the alternatives this decision is between.  "
            "If the topic already names options, include them "
            "faithfully; add any missing alternative worth considering "
            "(including 'do nothing' where it is a real choice).  Do "
            "not evaluate or rank yet — that comes later.\n\n"
            f"Options recorded so far:\n{options_text}\n\n"
            "Submit only options not already recorded by calling the "
            "submit_options tool with an array of option strings — "
            "each a distinct, self-contained alternative — plus a "
            "brief rationale in the 'reasoning' field."
        )

    def get_turn_prompt(self, entity: Entity,
                        discussion: Discussion) -> str:
        return (
            f"It is your turn, {entity.name}.  Enumerate the decision "
            "alternatives by calling the submit_options tool."
        )

    def get_summary_prompt(self, discussion: Discussion,
                           speaker_name: str,
                           next_speaker_name: str) -> str:
        return (
            f"{speaker_name} has proposed decision options.  Note any "
            "genuinely new alternatives, then invite "
            f"{next_speaker_name} to add options that are still missing."
        )

    # ------------------------------------------------------------------
    # Response processing (free-text / human fallback path)
    # ------------------------------------------------------------------

    def process_response(self, content: str, entity: Entity,
                         discussion: Discussion) -> ProcessedResponse:
        state = discussion.method_state
        items = parse_numbered_list(content, min_length=MIN_OPTION_LENGTH)
        if items:
            record_options(state, entity, items)
        else:
            logger.warning(
                "Could not extract options from %s's response", entity.name)
        return ProcessedResponse(display_content=content)

    # ------------------------------------------------------------------
    # Structured output (issue #23)
    # ------------------------------------------------------------------

    requires_structured_output = True

    def get_output_tool(self, entity: Entity,
                        discussion: Discussion) -> OutputToolSpec:
        return OutputToolSpec(
            name="submit_options",
            description=("Submit the decision alternatives as an array "
                         "of option strings, plus your reasoning."),
            parameters=OPTIONS_TOOL_PARAMETERS,
        )

    def validate_output(self, payload: dict, entity: Entity,
                        discussion: Discussion) -> str:
        return validate_options_payload(payload)

    def process_structured_response(self, payload: dict, entity: Entity,
                                    discussion: Discussion) -> ProcessedResponse:
        state = discussion.method_state
        texts = [str(o).strip() for o in payload["options"]
                 if str(o).strip()]
        accepted = record_options(state, entity, texts)
        reasoning = str(payload.get("reasoning") or "").strip()
        numbered = "\n".join(f"{n}. O{o['id']}: {o['text']}"
                             for n, o in enumerate(accepted, 1))
        display = f"{reasoning}\n\n{numbered}" if numbered else reasoning
        return ProcessedResponse(display_content=display)

    # ------------------------------------------------------------------
    # Phase advancement
    # ------------------------------------------------------------------

    def should_advance(self, discussion: Discussion) -> bool:
        state = discussion.method_state
        phase_round = state.get("phase_round", 1)
        if phase_round > MAX_OPTIONS_ROUNDS:
            logger.warning(
                "Option enumeration reached round %d; advancing with %d "
                "option(s) collected.",
                phase_round, len(state.get("options", [])),
            )
            return True
        return bool(state.get("options")) and phase_round > self.phase.rounds

    def _gave_up(self, discussion: Discussion) -> bool:
        """True if enumeration exhausted its rounds without any options."""
        state = discussion.method_state
        return (not state.get("options")
                and state.get("phase_round", 1) > MAX_OPTIONS_ROUNDS)

    def next_phase(self, discussion: Discussion) -> str | None:
        """Abort the method when enumeration produced nothing.

        Without options the remaining phases (criteria/score/
        sensitivity/decide) are degenerate — they would weigh and score
        an empty list and burn API spend producing nothing usable.
        """
        if self._gave_up(discussion):
            logger.warning(
                "Option enumeration produced no options — ending the "
                "decision-matrix method early")
            return None
        return LINEAR_NEXT

    def get_method_complete_message(self, discussion: Discussion) -> str:
        if not self._gave_up(discussion):
            return ""
        return (
            "⚠️ **Weighted Decision Matrix ended early.** The option "
            f"enumeration phase collected no usable options after "
            f"{MAX_OPTIONS_ROUNDS} rounds, so the criteria, scoring, "
            "and decision phases were skipped.  Consider rephrasing "
            "the topic as an explicit choice ('Should we do A, B, or "
            "C?') and starting a new discussion."
        )

    # ------------------------------------------------------------------
    # Transition message (when transitioning TO this phase)
    # ------------------------------------------------------------------

    def get_transition_message(self, discussion: Discussion) -> str:
        return (
            f"**Phase: {self.phase.display_name}**\n\n"
            "Participants will now enumerate the alternatives this "
            "decision is between.  Evaluation comes later — first the "
            "group needs the full option list."
        )
