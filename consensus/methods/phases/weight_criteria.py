"""Weighted-criteria phase handler for the Weighted Decision Matrix.

Participants jointly define the decision criteria and assign each an
importance weight via the forced ``submit_weighted_criteria`` output
tool (issue #23); a numbered-list-with-``(weight: N)`` free-text parse
remains the human/fallback path.  This generalises adversarial-collab's
``define_criteria`` (issue #25): criteria are merged across
participants by name similarity, and each participant's most recent
weight vote counts — the effective weight is the mean.  If no criteria
at all are collected after ``MAX_CRITERIA_ROUNDS``, the method aborts
early — scoring is impossible without criteria.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ..base import LINEAR_NEXT, OutputToolSpec, Phase, ProcessedResponse
from ..phase_handler import PhaseHandler
from ._mcda_analysis import format_criteria, format_options
from ._mcda_helpers import (
    CRITERIA_TOOL_PARAMETERS,
    MAX_CRITERIA_ROUNDS,
    WEIGHT_MAX,
    WEIGHT_MIN,
    extract_weighted_criteria,
    record_criteria,
    validate_criteria_payload,
)

if TYPE_CHECKING:
    from ...models import Discussion, Entity

logger = logging.getLogger(__name__)


class WeightCriteriaHandler(PhaseHandler):
    """Phase 2: Jointly define weighted decision criteria."""

    phase = Phase(
        name="criteria",
        display_name="Criteria & Weights",
        description=(
            "Participants jointly define the decision criteria and "
            "agree importance weights.  Criteria are locked before "
            "scoring begins."
        ),
        rounds=2,
    )

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    def init_state(self, discussion: Discussion) -> dict:
        return {"criteria": []}

    # ------------------------------------------------------------------
    # Prompts
    # ------------------------------------------------------------------

    def get_system_prompt(self, entity: Entity,
                          discussion: Discussion) -> str:
        state = discussion.method_state
        return (
            f"You are {entity.name}, participating in a Weighted "
            "Decision Matrix (multi-criteria decision analysis).\n"
            f"Topic: {discussion.topic}\n\n"
            "CRITERIA & WEIGHTS PHASE\n\n"
            "Define the criteria this decision should be judged by, "
            "and give each an importance weight from "
            f"{WEIGHT_MIN} (minor) to {WEIGHT_MAX} (decisive).  Good "
            "criteria are specific and measurable; weights should "
            "reflect how much each criterion matters, not how well "
            "any option performs on it.\n\n"
            f"Options under consideration:\n{format_options(state)}\n\n"
            f"Criteria proposed so far:\n{format_criteria(state)}\n\n"
            "Submit your criteria by calling the "
            "submit_weighted_criteria tool with an array of "
            "{name, weight} objects plus your reasoning.  Weight votes "
            "are averaged across participants; resubmitting a "
            "criterion updates your own vote on it.\n\n"
            "CRITICAL: These criteria and weights will be LOCKED "
            "before scoring.  You cannot change them later."
        )

    def get_turn_prompt(self, entity: Entity,
                        discussion: Discussion) -> str:
        round_num = discussion.method_state.get("phase_round", 1)
        if round_num == 1:
            return (
                f"It is your turn, {entity.name}.  Propose the decision "
                "criteria with importance weights by calling the "
                "submit_weighted_criteria tool."
            )
        return (
            f"Refinement round, {entity.name}.  Review the proposed "
            "criteria and weights: add anything missing, and revote "
            "where you disagree with a weight, by calling the "
            "submit_weighted_criteria tool."
        )

    def get_summary_prompt(self, discussion: Discussion,
                           speaker_name: str,
                           next_speaker_name: str) -> str:
        return (
            f"{speaker_name} has proposed/refined weighted criteria.  "
            "Note where weights diverge between participants.  "
            f"Next: {next_speaker_name}."
        )

    # ------------------------------------------------------------------
    # Response processing (free-text / human fallback path)
    # ------------------------------------------------------------------

    def process_response(self, content: str, entity: Entity,
                         discussion: Discussion) -> ProcessedResponse:
        state = discussion.method_state
        items = extract_weighted_criteria(content)
        if items:
            record_criteria(state, entity, items)
        else:
            logger.warning(
                "Could not extract weighted criteria from %s's response",
                entity.name)
        return ProcessedResponse(display_content=content)

    # ------------------------------------------------------------------
    # Structured output (issue #23)
    # ------------------------------------------------------------------

    requires_structured_output = True

    def get_output_tool(self, entity: Entity,
                        discussion: Discussion) -> OutputToolSpec:
        return OutputToolSpec(
            name="submit_weighted_criteria",
            description=("Submit the decision criteria as an array of "
                         "{name, weight, rationale} objects (weight "
                         f"{WEIGHT_MIN}-{WEIGHT_MAX}), plus your "
                         "reasoning."),
            parameters=CRITERIA_TOOL_PARAMETERS,
        )

    def validate_output(self, payload: dict, entity: Entity,
                        discussion: Discussion) -> str:
        return validate_criteria_payload(payload)

    def process_structured_response(self, payload: dict, entity: Entity,
                                    discussion: Discussion) -> ProcessedResponse:
        state = discussion.method_state
        touched = record_criteria(state, entity, payload["criteria"])
        reasoning = str(payload.get("reasoning") or "").strip()
        listing = "\n".join(
            f"C{c['id']}: {c['name']} — my weight "
            f"{c['weight_votes'].get(str(entity.id), '?')}"
            for c in touched)
        display = f"{reasoning}\n\n{listing}" if listing else reasoning
        return ProcessedResponse(display_content=display)

    # ------------------------------------------------------------------
    # Phase advancement
    # ------------------------------------------------------------------

    def should_advance(self, discussion: Discussion) -> bool:
        state = discussion.method_state
        phase_round = state.get("phase_round", 1)
        if phase_round > MAX_CRITERIA_ROUNDS:
            logger.warning(
                "Criteria definition reached round %d; advancing with "
                "%d criterion(s).",
                phase_round, len(state.get("criteria", [])),
            )
            return True
        return (bool(state.get("criteria"))
                and phase_round > self.phase.rounds)

    def _gave_up(self, discussion: Discussion) -> bool:
        """True if the phase exhausted its rounds without any criteria."""
        state = discussion.method_state
        return (not state.get("criteria")
                and state.get("phase_round", 1) > MAX_CRITERIA_ROUNDS)

    def next_phase(self, discussion: Discussion) -> str | None:
        """Abort the method when no criteria could be collected.

        Scoring, sensitivity, and the decision all need a criteria
        set; without one the remaining phases are degenerate.
        """
        if self._gave_up(discussion):
            logger.warning(
                "Criteria definition produced no criteria — ending the "
                "decision-matrix method early")
            return None
        return LINEAR_NEXT

    def get_method_complete_message(self, discussion: Discussion) -> str:
        if not self._gave_up(discussion):
            return ""
        return (
            "⚠️ **Weighted Decision Matrix ended early.** No usable "
            f"decision criteria were collected after "
            f"{MAX_CRITERIA_ROUNDS} rounds, so the scoring and "
            "decision phases were skipped."
        )

    # ------------------------------------------------------------------
    # Transition message (when transitioning TO this phase)
    # ------------------------------------------------------------------

    def get_transition_message(self, discussion: Discussion) -> str:
        n = len(discussion.method_state.get("options", []))
        return (
            f"**Phase: {self.phase.display_name}**\n\n"
            f"{n} option(s) are on the table.  Participants will now "
            "jointly define the decision criteria and agree importance "
            f"weights ({WEIGHT_MIN}-{WEIGHT_MAX}).  These will be "
            "LOCKED before scoring begins."
        )
