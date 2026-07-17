"""Belief-poll phase handler for Double Crux (pre-belief poll, 2026-07-17).

Runs on the factual path only, immediately after the moderator
identifies the shared crux and before crux testing.  Each disagreeing
party states their current probability that the *moderator's synthesized
shared claim* is true, via the forced ``submit_crux_belief`` output tool
(issue #23 pattern); free-text JSON-block parsing remains the
human/fallback path.  These polls become the authoritative
``initial_beliefs`` — the "before" end of the belief-shift metric —
fixing the coverage gap (all parties are polled, not just crux authors)
and the proposition mismatch (initial and final are both measured on the
moderator's claim).  When the phase completes, the polls are folded into
``shared_crux['initial_beliefs']`` deterministically.

Spec: docs/superpowers/specs/2026-07-17-double-crux-pre-belief-poll-design.md
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ..base import LINEAR_NEXT, OutputToolSpec, Phase, ProcessedResponse
from ..phase_handler import PhaseHandler
from ._crux_helpers import (
    MAX_POLL_ROUNDS,
    POLL_BELIEF_TOOL_PARAMETERS,
    apply_poll_beliefs,
    entities_with_poll,
    extract_poll_belief,
    format_shared_crux,
    record_poll_belief,
    validate_poll_belief_payload,
)

if TYPE_CHECKING:
    from ...models import Discussion, Entity

logger = logging.getLogger(__name__)


class PollBeliefHandler(PhaseHandler):
    """Phase 3.5: Each party polls their belief on the shared crux."""

    phase = Phase(
        name="poll_belief",
        display_name="Belief Poll",
        description=(
            "Each participant records their current probability that the "
            "shared crux claim is true, before evidence is presented — "
            "the baseline for measuring belief change."
        ),
        rounds=1,
    )

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    def init_state(self, discussion: Discussion) -> dict:
        return {"poll_beliefs": []}

    # ------------------------------------------------------------------
    # Prompts
    # ------------------------------------------------------------------

    def get_system_prompt(self, entity: Entity,
                          discussion: Discussion) -> str:
        state = discussion.method_state
        return (
            f"You are {entity.name}, participating in a Double Crux "
            "session.\n"
            f"Topic: {discussion.topic}\n\n"
            "BELIEF POLL PHASE\n\n"
            "The disagreement has been reduced to this crux:\n"
            f"{format_shared_crux(state)}\n\n"
            "Before any evidence is presented, state your current "
            "probability (0-1) that THIS exact claim is true, with a "
            "brief reason.  Answer for the claim as worded above — not a "
            "reframed version — because this number is the baseline "
            "against which any belief change from crux testing is "
            "measured.  Submit by calling the submit_crux_belief tool."
        )

    def get_turn_prompt(self, entity: Entity,
                        discussion: Discussion) -> str:
        return (
            f"It is your turn, {entity.name}.  State your current "
            "probability (0-1) that the shared crux claim is true by "
            "calling the submit_crux_belief tool."
        )

    def get_summary_prompt(self, discussion: Discussion,
                           speaker_name: str,
                           next_speaker_name: str) -> str:
        return (
            f"{speaker_name} has stated their current belief on the crux.  "
            "Note it neutrally — do not argue the claim yet.  Next: "
            f"{next_speaker_name}."
        )

    # ------------------------------------------------------------------
    # Response processing (free-text / human fallback path)
    # ------------------------------------------------------------------

    def process_response(self, content: str, entity: Entity,
                         discussion: Discussion) -> ProcessedResponse:
        state = discussion.method_state
        payload = extract_poll_belief(content)
        error = ("no belief found" if payload is None else
                 validate_poll_belief_payload(payload))
        if payload is not None and not error:
            record_poll_belief(state, entity, payload)
        else:
            logger.warning(
                "Could not extract a belief poll from %s's response (%s)",
                entity.name, error)
        return ProcessedResponse(display_content=content)

    # ------------------------------------------------------------------
    # Structured output (issue #23)
    # ------------------------------------------------------------------

    requires_structured_output = True

    def get_output_tool(self, entity: Entity,
                        discussion: Discussion) -> OutputToolSpec:
        return OutputToolSpec(
            name="submit_crux_belief",
            description=("Submit your current probability (0-1) that the "
                         "shared crux claim is true, plus your reasoning."),
            parameters=POLL_BELIEF_TOOL_PARAMETERS,
        )

    def validate_output(self, payload: dict, entity: Entity,
                        discussion: Discussion) -> str:
        return validate_poll_belief_payload(payload)

    def process_structured_response(self, payload: dict, entity: Entity,
                                    discussion: Discussion) -> ProcessedResponse:
        state = discussion.method_state
        record_poll_belief(state, entity, payload)
        reasoning = str(payload.get("reasoning") or "").strip()
        display = f"{reasoning}\n\nBelief on the crux: {payload['belief']}"
        return ProcessedResponse(display_content=display)

    # ------------------------------------------------------------------
    # Phase advancement
    # ------------------------------------------------------------------

    def should_advance(self, discussion: Discussion) -> bool:
        """Advance when every party has polled, or the cap is reached.

        Both ends of the belief-shift metric need every participant, so
        stragglers whose polls could not be parsed get further rounds
        (up to ``MAX_POLL_ROUNDS``).  When the roster is unknown (empty
        ``turn_order``) fall back to advancing once any poll has been
        recorded and a full round has run — mirrors ResolveCruxHandler.
        """
        state = discussion.method_state
        participant_ids = set(discussion.turn_order)
        if participant_ids and participant_ids.issubset(
                entities_with_poll(state)):
            return True
        phase_round = state.get("phase_round", 1)
        if phase_round > MAX_POLL_ROUNDS:
            logger.warning(
                "Belief poll reached round %d; advancing with %d "
                "belief(s) recorded.",
                phase_round, len(state.get("poll_beliefs", [])),
            )
            return True
        if participant_ids:
            return False  # roster known: keep waiting for stragglers
        return bool(state.get("poll_beliefs")) and phase_round > 1

    def next_phase(self, discussion: Discussion) -> str | None:
        """Fold the polls into initial_beliefs, then continue to testing.

        Done once, deterministically, so build_crux_map and the
        conclusion read poll-sourced initial beliefs (mirrors how
        ResolveCruxHandler builds the crux_map).
        """
        apply_poll_beliefs(discussion.method_state)
        return LINEAR_NEXT

    # ------------------------------------------------------------------
    # Transition message (when transitioning TO this phase)
    # ------------------------------------------------------------------

    def get_transition_message(self, discussion: Discussion) -> str:
        return (
            f"**Phase: {self.phase.display_name}**\n\n"
            "A shared factual crux has been identified.  Before testing "
            "it, each participant records their current probability that "
            "the crux claim is true — the baseline for measuring belief "
            "change."
        )
