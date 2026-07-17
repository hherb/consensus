"""Resolution phase handler for Double Crux (issue #27).

Each participant states their final position via the forced
``submit_resolution`` output tool (issue #23): whether crux testing
updated their position, their current position, and — when a factual
crux was tested — their current probability on the crux (the "after"
end of the belief-shift metric).  Free-text JSON-block parsing remains
the human/fallback path.  When the phase completes, the deterministic
``crux_map`` outcome artifact is assembled into method state.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ..base import LINEAR_NEXT, OutputToolSpec, Phase, ProcessedResponse
from ..phase_handler import PhaseHandler
from ._crux_artifact import build_crux_map, format_shared_crux
from ._crux_helpers import (
    MAX_RESOLVE_ROUNDS,
    RESOLUTION_TOOL_PARAMETERS,
    VERDICT_FACTUAL,
    VERDICT_VALUES,
    entities_with_resolutions,
    extract_resolution,
    record_resolution,
    validate_resolution_payload,
)

if TYPE_CHECKING:
    from ...models import Discussion, Entity

logger = logging.getLogger(__name__)


class ResolveCruxHandler(PhaseHandler):
    """Phase 5: Final positions, belief restatement, and the crux map."""

    phase = Phase(
        name="resolve",
        display_name="Resolution",
        description=(
            "Each participant states whether crux testing changed "
            "their position, their current position, and their "
            "current belief on the crux.  The outcome is either a "
            "resolution or a clean map of what the disagreement "
            "reduces to."
        ),
        rounds=1,
    )

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    def init_state(self, discussion: Discussion) -> dict:
        return {"resolutions": [], "crux_map": {}}

    # ------------------------------------------------------------------
    # Prompts
    # ------------------------------------------------------------------

    def _outcome_instructions(self, state: dict) -> str:
        """Verdict-specific instructions for the resolution turn."""
        verdict = state.get("crux_verdict", "")
        if verdict == VERDICT_FACTUAL:
            return (
                "A factual crux was tested:\n"
                f"{format_shared_crux(state)}\n\n"
                "State:\n"
                "1. 'stance' — did the evidence update your position?\n"
                "2. 'position' — your current position, stated fully\n"
                "3. 'crux_belief' — your current probability (0-1) that "
                "the crux claim is true.  Be honest: if the evidence "
                "moved you, your number should move\n"
                "4. 'reasoning' — what moved you, or why the evidence "
                "did not"
            )
        if verdict == VERDICT_VALUES:
            return (
                "The disagreement was found to reduce to a value "
                "difference, not a factual one:\n"
                f"{format_shared_crux(state)}\n\n"
                "State your 'stance', your current 'position', and in "
                "'reasoning' say what the disagreement reduces to from "
                "your side — a clean map of the value difference is a "
                "successful outcome."
            )
        return (
            "No shared crux was found within the search budget.  State "
            "your 'stance', your current 'position', and in 'reasoning' "
            "describe what the disagreement reduces to as precisely as "
            "you can — the residual map is still a useful outcome."
        )

    def get_system_prompt(self, entity: Entity,
                          discussion: Discussion) -> str:
        state = discussion.method_state
        return (
            f"You are {entity.name}, participating in a Double Crux "
            "session.\n"
            f"Topic: {discussion.topic}\n\n"
            "RESOLUTION PHASE\n\n"
            f"{self._outcome_instructions(state)}\n\n"
            "Submit by calling the submit_resolution tool."
        )

    def get_turn_prompt(self, entity: Entity,
                        discussion: Discussion) -> str:
        return (
            f"It is your turn, {entity.name}.  State your final "
            "position by calling the submit_resolution tool."
        )

    def get_summary_prompt(self, discussion: Discussion,
                           speaker_name: str,
                           next_speaker_name: str) -> str:
        return (
            f"{speaker_name} has stated their resolution.  Note whether "
            "they updated and where the parties now stand.  Next: "
            f"{next_speaker_name}."
        )

    # ------------------------------------------------------------------
    # Response processing (free-text / human fallback path)
    # ------------------------------------------------------------------

    def _require_belief(self, discussion: Discussion) -> bool:
        """Belief restatement is required only after a factual crux."""
        return (discussion.method_state.get("crux_verdict", "")
                == VERDICT_FACTUAL)

    def process_response(self, content: str, entity: Entity,
                         discussion: Discussion) -> ProcessedResponse:
        state = discussion.method_state
        payload = extract_resolution(content)
        # The free-text path never hard-requires the belief — a human
        # typing JSON should not be bounced for omitting a number.
        error = ("no resolution found" if payload is None else
                 validate_resolution_payload(payload, require_belief=False))
        if payload is not None and not error:
            record_resolution(state, entity, payload)
        else:
            logger.warning(
                "Could not extract a resolution from %s's response (%s)",
                entity.name, error)
        return ProcessedResponse(display_content=content)

    # ------------------------------------------------------------------
    # Structured output (issue #23)
    # ------------------------------------------------------------------

    requires_structured_output = True

    def get_output_tool(self, entity: Entity,
                        discussion: Discussion) -> OutputToolSpec:
        return OutputToolSpec(
            name="submit_resolution",
            description=("Submit your final position: stance "
                         "('updated'/'unchanged'), your current position, "
                         "your current probability on the shared crux "
                         "(when one was tested), and your reasoning."),
            parameters=RESOLUTION_TOOL_PARAMETERS,
        )

    def validate_output(self, payload: dict, entity: Entity,
                        discussion: Discussion) -> str:
        return validate_resolution_payload(
            payload, require_belief=self._require_belief(discussion))

    def process_structured_response(self, payload: dict, entity: Entity,
                                    discussion: Discussion) -> ProcessedResponse:
        state = discussion.method_state
        record_resolution(state, entity, payload)
        reasoning = str(payload.get("reasoning") or "").strip()
        belief = payload.get("crux_belief")
        belief_line = ("" if belief is None
                       else f"\nBelief on the crux: {belief}")
        display = (
            f"{reasoning}\n\n"
            f"Position ({payload['stance']}): {payload['position']}"
            f"{belief_line}"
        )
        return ProcessedResponse(display_content=display)

    # ------------------------------------------------------------------
    # Phase advancement
    # ------------------------------------------------------------------

    def should_advance(self, discussion: Discussion) -> bool:
        """Advance when every participant has a recorded resolution.

        The belief-shift metric needs both ends per participant, so
        stragglers whose resolutions could not be parsed get further
        rounds (up to ``MAX_RESOLVE_ROUNDS``) rather than being cut
        off after the first.  When the roster is unknown (empty
        ``turn_order``), fall back to advancing once any resolution
        has been recorded and a full round has run.
        """
        state = discussion.method_state
        participant_ids = set(discussion.turn_order)
        if participant_ids and participant_ids.issubset(
                entities_with_resolutions(state)):
            return True
        phase_round = state.get("phase_round", 1)
        if phase_round > MAX_RESOLVE_ROUNDS:
            logger.warning(
                "Resolution reached round %d; concluding with %d "
                "resolution(s) recorded.",
                phase_round, len(state.get("resolutions", [])),
            )
            return True
        if participant_ids:
            return False  # roster known: keep waiting for stragglers
        return bool(state.get("resolutions")) and phase_round > 1

    def next_phase(self, discussion: Discussion) -> str | None:
        """Assemble the crux_map artifact, then end (resolve is last).

        The map is built here — once, deterministically — so both the
        conclusion prompt and any downstream consumer read the same
        numbers (mirrors the MCDA decision_artifact pattern).
        """
        state = discussion.method_state
        state["crux_map"] = build_crux_map(state)
        return LINEAR_NEXT
