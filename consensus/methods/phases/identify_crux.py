"""Shared-crux identification phase handler for Double Crux (issue #27).

A moderator-only phase (see cluster_ideas.py for the pattern): the
moderator studies the submitted cruxes and issues a verdict via the
forced ``submit_crux_selection`` output tool (issue #23):

- ``factual`` — a shared factual crux exists → continue linearly to
  crux testing;
- ``values`` — the disagreement reduces to a value difference → jump
  straight to resolution (nothing factual to test);
- ``none`` — no shared crux yet → loop back to crux hunting (the
  issue-#22 ``next_phase`` mechanism), bounded by
  ``MAX_CRUX_SEARCH_ROUNDS``; when exhausted, the verdict is forced to
  ``none`` and the method proceeds to resolution to report a clean
  disagreement map.

Free-text JSON-block parsing remains the fallback path, gated by
``MAX_IDENTIFY_ATTEMPTS``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ..base import LINEAR_NEXT, OutputToolSpec, Phase, ProcessedResponse
from ..phase_handler import PhaseHandler
from ._crux_helpers import (
    CRUX_SELECTION_TOOL_PARAMETERS,
    MAX_CRUX_SEARCH_ROUNDS,
    MAX_IDENTIFY_ATTEMPTS,
    VERDICT_NONE,
    extract_crux_selection,
    format_cruxes,
    format_positions,
    format_shared_crux,
    record_crux_selection,
    validate_crux_selection_payload,
)

if TYPE_CHECKING:
    from ...models import Discussion, Entity

logger = logging.getLogger(__name__)


class IdentifyCruxHandler(PhaseHandler):
    """Phase 3: The moderator identifies the shared crux (or its absence)."""

    phase = Phase(
        name="identify_crux",
        display_name="Crux Identification",
        description=(
            "The moderator compares the submitted cruxes and determines "
            "whether a shared factual crux exists, the disagreement "
            "reduces to a value difference, or more hunting is needed."
        ),
        rounds=0,  # condition-based: verdict recorded or attempts exhausted
    )

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    def init_state(self, discussion: Discussion) -> dict:
        return {"crux_verdict": "", "shared_crux": {},
                "identify_attempts": 0, "crux_search_rounds": 1}

    # ------------------------------------------------------------------
    # Turn order — moderator only
    # ------------------------------------------------------------------

    def get_turn_order(self, entity_ids: list[int],
                       discussion: Discussion) -> list[int]:
        """Only the moderator speaks during crux identification."""
        return [discussion.moderator_id]

    # ------------------------------------------------------------------
    # Prompts
    # ------------------------------------------------------------------

    def get_system_prompt(self, entity: Entity,
                          discussion: Discussion) -> str:
        state = discussion.method_state
        return (
            "You are the moderator of a Double Crux session, looking "
            "for the underlying belief that actually drives the "
            "disagreement.\n"
            f"Topic: {discussion.topic}\n\n"
            "CRUX IDENTIFICATION PHASE\n\n"
            "Compare the submitted cruxes and decide:\n"
            "- verdict 'factual': at least two participants' positions "
            "pivot on the same checkable claim (they need not agree on "
            "it — disagreeing about a shared pivotal claim is exactly "
            "what a crux is).  State it as ONE neutral claim and cite "
            "the crux ids it comes from.\n"
            "- verdict 'values': the positions rest on different values "
            "or priorities rather than a factual dispute.  State the "
            "value difference.\n"
            "- verdict 'none': no shared crux is visible yet — another "
            "hunting round will be run.\n\n"
            f"Positions:\n{format_positions(state)}\n\n"
            f"Submitted cruxes:\n{format_cruxes(state)}"
        )

    def get_turn_prompt(self, entity: Entity,
                        discussion: Discussion) -> str:
        state = discussion.method_state
        if state.get("identify_attempts", 0) > 0:
            return (
                "The previous selection was not usable.  Please call "
                "the submit_crux_selection tool with your verdict "
                "('factual', 'values', or 'none'), the supporting "
                "crux_ids and claim where applicable, and your reasoning."
            )
        return (
            "Identify the shared crux by calling the "
            "submit_crux_selection tool: verdict 'factual' with "
            "crux_ids and a neutral claim, verdict 'values' with the "
            "value difference, or verdict 'none' if more hunting is "
            "needed."
        )

    # ------------------------------------------------------------------
    # Response processing (free-text / fallback path)
    # ------------------------------------------------------------------

    def process_response(self, content: str, entity: Entity,
                         discussion: Discussion) -> ProcessedResponse:
        state = discussion.method_state
        payload = extract_crux_selection(content)
        error = ("no verdict found" if payload is None else
                 validate_crux_selection_payload(
                     payload, {c["id"] for c in state.get("cruxes", [])}))
        if payload is not None and not error:
            record_crux_selection(state, payload)
            logger.info("Recorded crux verdict %r from free text",
                        state["crux_verdict"])
        else:
            state["identify_attempts"] = state.get("identify_attempts",
                                                   0) + 1
            logger.warning(
                "Crux identification attempt %d failed (%s)",
                state["identify_attempts"], error)
        return ProcessedResponse(display_content=content)

    # ------------------------------------------------------------------
    # Structured output (issue #23)
    # ------------------------------------------------------------------

    requires_structured_output = True

    def get_output_tool(self, entity: Entity,
                        discussion: Discussion) -> OutputToolSpec:
        return OutputToolSpec(
            name="submit_crux_selection",
            description=("Submit your crux verdict: 'factual' (with "
                         "crux_ids and one neutral claim), 'values' (with "
                         "the value difference), or 'none', plus your "
                         "reasoning."),
            parameters=CRUX_SELECTION_TOOL_PARAMETERS,
        )

    def validate_output(self, payload: dict, entity: Entity,
                        discussion: Discussion) -> str:
        valid_ids = {c["id"]
                     for c in discussion.method_state.get("cruxes", [])}
        return validate_crux_selection_payload(payload, valid_ids)

    def process_structured_response(self, payload: dict, entity: Entity,
                                    discussion: Discussion) -> ProcessedResponse:
        state = discussion.method_state
        record_crux_selection(state, payload)
        logger.info("Recorded crux verdict %r from structured output",
                    state["crux_verdict"])
        reasoning = str(payload.get("reasoning") or "").strip()
        display = f"{reasoning}\n\n{format_shared_crux(state)}"
        return ProcessedResponse(display_content=display)

    # ------------------------------------------------------------------
    # Phase advancement & routing
    # ------------------------------------------------------------------

    def should_advance(self, discussion: Discussion) -> bool:
        state = discussion.method_state
        if state.get("crux_verdict"):
            return True
        if state.get("identify_attempts", 0) >= MAX_IDENTIFY_ATTEMPTS:
            logger.warning(
                "Giving up on crux identification after %d attempts",
                MAX_IDENTIFY_ATTEMPTS)
            return True
        return False

    def next_phase(self, discussion: Discussion) -> str | None:
        """Route on the verdict; loop back to hunting on 'none'.

        An exhausted give-up (no verdict recorded) is treated as
        'none'.  While search rounds remain, the verdict and attempt
        counter are reset and the method jumps back to ``hunt_cruxes``;
        once ``MAX_CRUX_SEARCH_ROUNDS`` is reached the verdict is
        finalised as 'none' and resolution still runs — the method
        reports a clean disagreement map instead of a resolution.
        """
        state = discussion.method_state
        verdict = state.get("crux_verdict", "")
        if verdict == "" or verdict == VERDICT_NONE:
            if state.get("crux_search_rounds", 1) < MAX_CRUX_SEARCH_ROUNDS:
                state["crux_search_rounds"] = (
                    state.get("crux_search_rounds", 1) + 1)
                state["crux_verdict"] = ""
                state["identify_attempts"] = 0
                logger.info(
                    "No shared crux yet — starting crux search round %d",
                    state["crux_search_rounds"])
                return "hunt_cruxes"
            state["crux_verdict"] = VERDICT_NONE
            state.setdefault("shared_crux", {})
            logger.warning(
                "No shared crux after %d search rounds — proceeding to "
                "resolution with a disagreement map",
                MAX_CRUX_SEARCH_ROUNDS)
            return "resolve"
        if verdict == "values":
            # Nothing factual to test — jump straight to resolution.
            return "resolve"
        return LINEAR_NEXT  # factual → test_crux

    # ------------------------------------------------------------------
    # Transition message (when transitioning TO this phase)
    # ------------------------------------------------------------------

    def get_transition_message(self, discussion: Discussion) -> str:
        n = len(discussion.method_state.get("cruxes", []))
        return (
            f"**Phase: {self.phase.display_name}**\n\n"
            f"Crux hunting is complete — {n} candidate crux(es) are on "
            "the table.  The moderator will now determine whether a "
            "shared factual crux exists, the disagreement is a value "
            "difference, or another hunting round is needed."
        )
