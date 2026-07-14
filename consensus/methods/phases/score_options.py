"""Option-scoring phase handler for the Weighted Decision Matrix.

Each participant scores every option against every criterion on an
integer scale via the forced ``submit_scores`` output tool (issue #23);
a fenced/inline-JSON free-text parse remains the human/fallback path.
This generalises ACH's ``evaluate_matrix`` (issue #25): two levels of
dynamic keys (option label × criterion label) via
``additionalProperties``, partial coverage allowed (unscored cells
default to the scale midpoint during aggregation).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ..base import OutputToolSpec, Phase, ProcessedResponse
from ..phase_handler import PhaseHandler
from ._mcda_analysis import format_criteria, format_options, format_score_table
from ._mcda_helpers import (
    SCORE_MAX,
    SCORE_MIN,
    SCORES_TOOL_PARAMETERS,
    extract_scores,
    record_scores,
    validate_scores_payload,
)

if TYPE_CHECKING:
    from ...models import Discussion, Entity

logger = logging.getLogger(__name__)


class ScoreOptionsHandler(PhaseHandler):
    """Phase 3: Score every option against every criterion."""

    phase = Phase(
        name="score",
        display_name="Scoring",
        description=(
            "Each participant scores every option against every "
            "criterion.  Scores express performance on the criterion, "
            "independent of its weight."
        ),
        rounds=1,
    )

    # ------------------------------------------------------------------
    # Prompts
    # ------------------------------------------------------------------

    def get_system_prompt(self, entity: Entity,
                          discussion: Discussion) -> str:
        state = discussion.method_state
        base = (
            f"You are {entity.name}, participating in a Weighted "
            "Decision Matrix (multi-criteria decision analysis).\n"
            f"Topic: {discussion.topic}\n\n"
        )
        # Degenerate matrix (no options or no criteria): get_output_tool
        # returns None, so no submit_scores tool is offered — don't
        # instruct the model to call it (evaluate_matrix.py pattern).
        if not state.get("options") or not state.get("criteria"):
            return base + (
                "SCORING PHASE\n\n"
                "No scoring matrix could be formed (missing options or "
                "criteria).  Give a brief qualitative assessment of the "
                "alternatives instead."
            )
        return base + (
            "SCORING PHASE\n\n"
            f"Options:\n{format_options(state)}\n\n"
            f"Criteria (weights locked):\n{format_criteria(state)}\n\n"
            f"Score EACH option against EACH criterion from "
            f"{SCORE_MIN} (poor) to {SCORE_MAX} (excellent).  Judge "
            "performance on the criterion only — the weights already "
            "capture importance.\n\n"
            "Submit your scores by calling the submit_scores tool, "
            "mapping each option label (O1, O2, ...) to an object of "
            "criterion labels (C1, C2, ...) to your integer score, "
            "plus your reasoning — explain the extremes: why does an "
            "option score highest or lowest on a criterion?"
        )

    def get_turn_prompt(self, entity: Entity,
                        discussion: Discussion) -> str:
        state = discussion.method_state
        if not state.get("options") or not state.get("criteria"):
            return (
                f"{entity.name}, no scoring matrix could be formed — "
                "give a brief qualitative assessment instead."
            )
        return (
            f"{entity.name}, score every option against every "
            f"criterion ({SCORE_MIN}-{SCORE_MAX}) by calling the "
            "submit_scores tool with your scores and reasoning."
        )

    def get_summary_prompt(self, discussion: Discussion,
                           speaker_name: str,
                           next_speaker_name: str) -> str:
        return (
            f"{speaker_name} has submitted their scores.  Note any "
            "scores that differ significantly from previous "
            f"participants.  Next: {next_speaker_name}."
        )

    # ------------------------------------------------------------------
    # Response processing (free-text / human fallback path)
    # ------------------------------------------------------------------

    def process_response(self, content: str, entity: Entity,
                         discussion: Discussion) -> ProcessedResponse:
        state = discussion.method_state
        scores = extract_scores(content)
        kept = record_scores(state, entity, scores) if scores else 0
        if kept:
            table = format_score_table(
                state["scores"][str(entity.id)], state)
            display = f"{content}\n\n---\n{table}"
        else:
            logger.warning(
                "Could not extract scores from %s's response", entity.name)
            display = content
        return ProcessedResponse(display_content=display)

    # ------------------------------------------------------------------
    # Structured output (issue #23)
    # ------------------------------------------------------------------

    requires_structured_output = True

    def get_output_tool(self, entity: Entity,
                        discussion: Discussion) -> OutputToolSpec | None:
        """Declare the forced submit_scores tool for this phase.

        Returns ``None`` when there is nothing to score (no options or
        no criteria): no payload could pass validation, so forcing the
        tool would burn every retry (evaluate_matrix.py pattern).
        """
        state = discussion.method_state
        if not state.get("options") or not state.get("criteria"):
            return None
        return OutputToolSpec(
            name="submit_scores",
            description=(
                "Submit your score for each option against each "
                f"criterion ({SCORE_MIN}-{SCORE_MAX}), plus your "
                "reasoning.\n"
                f"Options:\n{format_options(state)}\n\n"
                f"Criteria:\n{format_criteria(state)}"
            ),
            parameters=SCORES_TOOL_PARAMETERS,
        )

    def validate_output(self, payload: dict, entity: Entity,
                        discussion: Discussion) -> str:
        state = discussion.method_state
        return validate_scores_payload(payload, state.get("options", []),
                                       state.get("criteria", []))

    def process_structured_response(self, payload: dict, entity: Entity,
                                    discussion: Discussion) -> ProcessedResponse:
        """Store the submitted scores and render the score table.

        Writes ``state["scores"][str(entity.id)]`` in exactly the
        shape ``extract_scores`` produces, so the aggregation in
        ``_mcda_analysis`` works regardless of which path a turn took.
        """
        state = discussion.method_state
        record_scores(state, entity, payload["scores"])
        table = format_score_table(
            state.get("scores", {}).get(str(entity.id), {}), state)
        reasoning = str(payload.get("reasoning") or "").strip()
        display = f"{reasoning}\n\n---\n{table}" if reasoning else table
        return ProcessedResponse(display_content=display)

    # ------------------------------------------------------------------
    # Phase advancement
    # ------------------------------------------------------------------

    def should_advance(self, discussion: Discussion) -> bool:
        return discussion.method_state.get("phase_round", 1) > self.phase.rounds

    # ------------------------------------------------------------------
    # Transition message (when transitioning TO this phase)
    # ------------------------------------------------------------------

    def get_transition_message(self, discussion: Discussion) -> str:
        state = discussion.method_state
        return (
            f"**Phase: {self.phase.display_name}**\n\n"
            "Criteria and weights are locked:\n"
            f"{format_criteria(state)}\n\n"
            "Each participant will now score every option against "
            f"every criterion ({SCORE_MIN}-{SCORE_MAX})."
        )
