"""Decision-capture phase handler for the Weighted Decision Matrix.

A moderator-only phase (the moderator takes a real turn — see
frame_hypotheses.py for the pattern) that records the final decision
via the forced ``submit_decision`` output tool (issue #23) and
assembles the machine-readable decision artifact (issue #25) into
``method_state["decision_artifact"]``.  The free-text fallback also
records an artifact — the recommendation defaults to the top-ranked
option with a caveat noting the default — so the artifact always
exists for the storyboard, the MCP server, or a follow-up discussion.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ..base import OutputToolSpec, Phase, ProcessedResponse
from ..phase_handler import PhaseHandler
from ._mcda_analysis import (
    build_decision_artifact,
    format_decision_artifact,
    format_divergence,
    format_sensitivity,
    format_weighted_ranking,
    ranked_options,
)
from ._mcda_helpers import DECISION_TOOL_PARAMETERS, validate_decision_payload

if TYPE_CHECKING:
    from ...models import Discussion, Entity

logger = logging.getLogger(__name__)


class DecideHandler(PhaseHandler):
    """Phase 5: Moderator records the decision and its artifact."""

    phase = Phase(
        name="decide",
        display_name="Decision",
        description=(
            "The moderator records the recommended option with its "
            "rationale and caveats, producing a structured, "
            "machine-readable decision artifact."
        ),
        rounds=1,
    )

    # ------------------------------------------------------------------
    # Turn order — moderator only
    # ------------------------------------------------------------------

    def get_turn_order(self, entity_ids: list[int],
                       discussion: Discussion) -> list[int]:
        """Only the moderator speaks when recording the decision."""
        return [discussion.moderator_id]

    # ------------------------------------------------------------------
    # Prompts
    # ------------------------------------------------------------------

    def get_system_prompt(self, entity: Entity,
                          discussion: Discussion) -> str:
        state = discussion.method_state
        base = (
            "You are the moderator of a Weighted Decision Matrix "
            "(multi-criteria decision analysis) session, recording "
            "the final decision.\n"
            f"Topic: {discussion.topic}\n\n"
            "DECISION PHASE\n\n"
        )
        if not state.get("options"):
            return base + (
                "No options were recorded, so there is nothing to "
                "decide.  Briefly summarise why the process could not "
                "reach a decision."
            )
        return base + (
            f"Weighted ranking:\n{format_weighted_ranking(state)}\n\n"
            f"Sensitivity findings:\n{format_sensitivity(state)}\n\n"
            f"Participant divergence:\n{format_divergence(state)}\n\n"
            "Record the decision by calling the submit_decision tool "
            "with the recommended option's numeric id, a rationale "
            "grounded in the weighted results and sensitivity "
            "findings, and any caveats (close calls, pivotal "
            "criteria, strong divergence).  Normally the top-ranked "
            "option is recommended; if you recommend a different one, "
            "the rationale must justify the departure explicitly."
        )

    def get_turn_prompt(self, entity: Entity,
                        discussion: Discussion) -> str:
        state = discussion.method_state
        if not state.get("options"):
            return ("Summarise briefly why no decision could be "
                    "reached.")
        return (
            "Record the final decision now by calling the "
            "submit_decision tool with the recommended option id, "
            "rationale, and caveats."
        )

    # ------------------------------------------------------------------
    # Response processing (free-text / human fallback path)
    # ------------------------------------------------------------------

    def process_response(self, content: str, entity: Entity,
                         discussion: Discussion) -> ProcessedResponse:
        """Record a fallback artifact from a free-text moderator turn.

        The recommendation defaults to the top-ranked option — the
        moderator's text becomes the rationale, with a caveat noting
        the default — so the artifact exists on both paths.  With no
        options there is nothing to decide, and no artifact.
        """
        state = discussion.method_state
        ranking = ranked_options(state)
        if not ranking:
            return ProcessedResponse(display_content=content)
        artifact = build_decision_artifact(
            state, ranking[0]["id"], content.strip(),
            ["Recommendation defaulted to the top-ranked option (the "
             "moderator turn was free text)."])
        logger.info("Recorded fallback decision artifact for option %d",
                    artifact["recommended_option_id"])
        display = f"{content}\n\n---\n{format_decision_artifact(artifact)}"
        return ProcessedResponse(display_content=display)

    # ------------------------------------------------------------------
    # Structured output (issue #23)
    # ------------------------------------------------------------------

    requires_structured_output = True

    def get_output_tool(self, entity: Entity,
                        discussion: Discussion) -> OutputToolSpec | None:
        """Declare the forced submit_decision tool for this phase.

        Returns ``None`` when no options exist (nothing to decide —
        no payload could pass validation, so forcing the tool would
        burn every retry).
        """
        state = discussion.method_state
        if not state.get("options"):
            return None
        return OutputToolSpec(
            name="submit_decision",
            description=("Record the final decision: the recommended "
                         "option's numeric id, your rationale, and any "
                         "caveats.\nWeighted ranking:\n"
                         + format_weighted_ranking(state)),
            parameters=DECISION_TOOL_PARAMETERS,
        )

    def validate_output(self, payload: dict, entity: Entity,
                        discussion: Discussion) -> str:
        state = discussion.method_state
        valid_ids = {o["id"] for o in state.get("options", [])}
        return validate_decision_payload(payload, valid_ids)

    def process_structured_response(self, payload: dict, entity: Entity,
                                    discussion: Discussion) -> ProcessedResponse:
        state = discussion.method_state
        caveats = [str(c) for c in (payload.get("caveats") or [])]
        artifact = build_decision_artifact(
            state, int(payload["recommended_option_id"]),
            str(payload["rationale"]).strip(), caveats)
        logger.info("Recorded decision artifact for option %d",
                    artifact["recommended_option_id"])
        return ProcessedResponse(
            display_content=format_decision_artifact(artifact))

    # ------------------------------------------------------------------
    # Phase advancement
    # ------------------------------------------------------------------

    def should_advance(self, discussion: Discussion) -> bool:
        return discussion.method_state.get("phase_round", 1) > self.phase.rounds

    # ------------------------------------------------------------------
    # Transition message (when transitioning TO this phase)
    # ------------------------------------------------------------------

    def get_transition_message(self, discussion: Discussion) -> str:
        return (
            f"**Phase: {self.phase.display_name}**\n\n"
            "The analysis is complete.  The moderator will now record "
            "the final decision with its rationale and caveats."
        )
