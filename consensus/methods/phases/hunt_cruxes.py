"""Crux hunting phase handler for Double Crux (issue #27).

Each participant answers the canonical Double Crux question — "what
factual claim, if you were wrong about it, would change your mind?" —
via the forced ``submit_cruxes`` output tool (issue #23), stating a
current belief probability per crux (the "before" end of the
belief-shift metric).  Free-text JSON-block / numbered-list parsing
remains the human/fallback path.  On later search rounds (the identify
phase loops back here when no shared crux was found) participants are
asked to engage with the other side's cruxes and converge.  If no
cruxes at all are collected after ``MAX_HUNT_ROUNDS``, the method
aborts early — every later phase needs a crux list.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ..base import LINEAR_NEXT, OutputToolSpec, Phase, ProcessedResponse
from ..phase_handler import PhaseHandler
from ._crux_helpers import (
    CRUXES_TOOL_PARAMETERS,
    MAX_HUNT_ROUNDS,
    extract_cruxes,
    record_cruxes,
    validate_cruxes_payload,
)

if TYPE_CHECKING:
    from ...models import Discussion, Entity

logger = logging.getLogger(__name__)


class HuntCruxesHandler(PhaseHandler):
    """Phase 2: Each participant surfaces their candidate cruxes."""

    phase = Phase(
        name="hunt_cruxes",
        display_name="Crux Hunting",
        description=(
            "Each participant identifies the factual claims their "
            "position actually rests on: claims that, if they were "
            "wrong about them, would change their mind."
        ),
        rounds=1,
    )

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    def init_state(self, discussion: Discussion) -> dict:
        return {"cruxes": []}

    # ------------------------------------------------------------------
    # Prompts
    # ------------------------------------------------------------------

    def get_system_prompt(self, entity: Entity,
                          discussion: Discussion) -> str:
        base = (
            f"You are {entity.name}, participating in a Double Crux "
            "session — a disagreement-resolution format that searches "
            "for the underlying belief driving the disagreement.\n"
            f"Topic: {discussion.topic}\n\n"
            "CRUX HUNTING PHASE\n\n"
            "Ask yourself: what factual claim, if you were wrong about "
            "it, would change your mind on this topic?  A good crux is:\n"
            "1. Specific and checkable (a factual claim, not a value)\n"
            "2. Genuinely load-bearing — being wrong about it really "
            "would move you\n"
            "3. Stated with your current probability that it is true\n\n"
            "Submit 1-5 cruxes by calling the submit_cruxes tool: each "
            "with a 'claim', your 'belief' (0-1 probability the claim "
            "is true), and 'why_pivotal'."
        )
        state = discussion.method_state
        if state.get("crux_search_rounds", 1) > 1:
            base += (
                "\n\nNo shared crux emerged from the previous round.  "
                "Read the cruxes the other participants proposed and "
                "look for common ground: state your belief on THEIR "
                "pivotal claims, or reformulate your own cruxes so a "
                "shared one can be found."
            )
        return base

    def get_turn_prompt(self, entity: Entity,
                        discussion: Discussion) -> str:
        return (
            f"It is your turn, {entity.name}.  What factual claim, if "
            "you were wrong about it, would change your mind?  Submit "
            "your cruxes by calling the submit_cruxes tool."
        )

    def get_summary_prompt(self, discussion: Discussion,
                           speaker_name: str,
                           next_speaker_name: str) -> str:
        return (
            f"{speaker_name} has stated the claims their position rests "
            "on.  Briefly note where their cruxes touch claims already "
            "on the table — do not evaluate them yet.  Next: "
            f"{next_speaker_name}."
        )

    # ------------------------------------------------------------------
    # Response processing (free-text / human fallback path)
    # ------------------------------------------------------------------

    def process_response(self, content: str, entity: Entity,
                         discussion: Discussion) -> ProcessedResponse:
        state = discussion.method_state
        items = extract_cruxes(content)
        if items:
            record_cruxes(state, entity, items)
        else:
            logger.warning(
                "Could not extract cruxes from %s's response", entity.name)
        return ProcessedResponse(display_content=content)

    # ------------------------------------------------------------------
    # Structured output (issue #23)
    # ------------------------------------------------------------------

    requires_structured_output = True

    def get_output_tool(self, entity: Entity,
                        discussion: Discussion) -> OutputToolSpec:
        return OutputToolSpec(
            name="submit_cruxes",
            description=("Submit the factual claims your position rests "
                         "on: an array of {claim, belief, why_pivotal} "
                         "objects, plus your reasoning."),
            parameters=CRUXES_TOOL_PARAMETERS,
        )

    def validate_output(self, payload: dict, entity: Entity,
                        discussion: Discussion) -> str:
        return validate_cruxes_payload(payload)

    def process_structured_response(self, payload: dict, entity: Entity,
                                    discussion: Discussion) -> ProcessedResponse:
        state = discussion.method_state
        accepted = record_cruxes(state, entity, payload["cruxes"])
        reasoning = str(payload.get("reasoning") or "").strip()
        listing = "\n".join(
            f"{n}. {c['claim']} (belief: {c['belief']}) — {c['why_pivotal']}"
            for n, c in enumerate(accepted, 1))
        display = f"{reasoning}\n\n{listing}" if listing else reasoning
        return ProcessedResponse(display_content=display)

    # ------------------------------------------------------------------
    # Phase advancement
    # ------------------------------------------------------------------

    def should_advance(self, discussion: Discussion) -> bool:
        state = discussion.method_state
        phase_round = state.get("phase_round", 1)
        if phase_round > MAX_HUNT_ROUNDS:
            logger.warning(
                "Crux hunting reached round %d; advancing with %d "
                "crux(es) collected.",
                phase_round, len(state.get("cruxes", [])),
            )
            return True
        return bool(state.get("cruxes")) and phase_round > 1

    def _gave_up(self, discussion: Discussion) -> bool:
        """True if hunting exhausted its rounds without any cruxes."""
        state = discussion.method_state
        return (not state.get("cruxes")
                and state.get("phase_round", 1) > MAX_HUNT_ROUNDS)

    def next_phase(self, discussion: Discussion) -> str | None:
        """Abort the method when hunting produced no cruxes at all.

        Without cruxes the remaining phases (identify/test/resolve)
        are degenerate — there is nothing to select, test, or update
        beliefs on.
        """
        if self._gave_up(discussion):
            logger.warning(
                "Crux hunting produced no cruxes — ending the Double "
                "Crux method early")
            return None
        return LINEAR_NEXT

    def get_method_complete_message(self, discussion: Discussion) -> str:
        if not self._gave_up(discussion):
            return ""
        return (
            "⚠️ **Double Crux ended early.** The crux hunting phase "
            f"collected no usable cruxes after {MAX_HUNT_ROUNDS} rounds, "
            "so the identification, testing, and resolution phases were "
            "skipped.  Consider restating the topic as a concrete "
            "yes/no question the parties actually disagree on."
        )
