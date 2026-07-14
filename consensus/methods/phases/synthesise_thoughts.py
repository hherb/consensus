"""Synthesis phase handler for Tree of Thoughts (issue #26).

A moderator-only presentational phase: the exploration is over (the
prune phase routed here on convergence, a degenerate beam, or the
depth budget) and the deterministic outcome artifact is already in
``method_state["tot_artifact"]``.  The moderator presents the outcome
in one free-text turn; the method then completes linearly and the
conclusion prompt drives the full synthesis.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ..base import Phase, ProcessedResponse
from ..phase_handler import PhaseHandler
from ._tot_analysis import format_beam_trajectory, format_expansions

if TYPE_CHECKING:
    from ...models import Discussion, Entity

logger = logging.getLogger(__name__)


def _artifact_summary(state: dict) -> str:
    """Human-readable digest of the outcome artifact for prompts."""
    artifact = state.get("tot_artifact", {})
    recommendation = artifact.get("recommendation") or {}
    lines = [
        f"Stop reason: {artifact.get('stop_reason', 'unknown')} after "
        f"{artifact.get('depth', 0)} prune pass(es).",
        "Final beam:",
    ]
    for entry in artifact.get("final_beam", []):
        lines.append(f"  T{entry['id']} (composite {entry['composite']}, "
                     f"{entry['scorer_count']} scorer(s)): {entry['text']}")
    if recommendation:
        lines.append(f"Top-ranked approach: T{recommendation['id']} "
                     f"(composite {recommendation['composite']}): "
                     f"{recommendation['text']}")
    lines.append(f"Beam trajectory:\n{format_beam_trajectory(state)}")
    caveats = artifact.get("caveats", [])
    if caveats:
        lines.append("Caveats:")
        lines.extend(f"  - {c}" for c in caveats)
    return "\n".join(lines)


class SynthesiseThoughtsHandler(PhaseHandler):
    """Phase 5: The moderator presents the exploration's outcome."""

    phase = Phase(
        name="synthesise",
        display_name="Synthesis",
        description=(
            "The exploration is complete.  The moderator presents the "
            "surviving approaches, their score trajectories, and the "
            "known obstacles."
        ),
        rounds=1,
    )

    # ------------------------------------------------------------------
    # Turn order — moderator only
    # ------------------------------------------------------------------

    def get_turn_order(self, entity_ids: list[int],
                       discussion: Discussion) -> list[int]:
        """Only the moderator speaks during synthesis."""
        return [discussion.moderator_id]

    # ------------------------------------------------------------------
    # Prompts
    # ------------------------------------------------------------------

    def get_system_prompt(self, entity: Entity,
                          discussion: Discussion) -> str:
        state = discussion.method_state
        depth = len(state.get("beam_history", []))
        return (
            "You are the moderator of a Tree of Thoughts session.\n"
            f"Topic: {discussion.topic}\n\n"
            "SYNTHESIS PHASE\n\n"
            "The exploration is complete.  All numbers below were "
            "computed deterministically from the participants' scores — "
            "do not alter them.\n\n"
            f"{_artifact_summary(state)}\n\n"
            "Recorded deep-dives from the final pass:\n"
            f"{format_expansions(state, max(depth - 1, 1))}\n\n"
            "Present the outcome to the group: the surviving "
            "approaches, how their standing evolved across passes, the "
            "obstacles that emerged, and what the composite scores say "
            "about the recommendation.  Keep it factual — quote the "
            "numbers above."
        )

    def get_turn_prompt(self, entity: Entity,
                        discussion: Discussion) -> str:
        return (
            "Present the exploration's outcome to the group, quoting "
            "the computed composites and the beam trajectory."
        )

    # ------------------------------------------------------------------
    # Response processing — presentational, nothing to extract
    # ------------------------------------------------------------------

    def process_response(self, content: str, entity: Entity,
                         discussion: Discussion) -> ProcessedResponse:
        return ProcessedResponse(display_content=content)

    # ------------------------------------------------------------------
    # Transition message (when transitioning TO this phase)
    # ------------------------------------------------------------------

    def get_transition_message(self, discussion: Discussion) -> str:
        artifact = discussion.method_state.get("tot_artifact", {})
        reason = artifact.get("stop_reason", "")
        reason_text = {
            "converged": "the beam stabilised — further passes would "
                         "change nothing",
            "depth_budget": "the depth budget was spent",
            "degenerate": "too few approaches survived to keep exploring "
                          "in parallel",
        }.get(reason, "the exploration ended")
        return (
            f"**Phase: {self.phase.display_name}**\n\n"
            f"The exploration has ended: {reason_text}.  The moderator "
            "will now present the outcome."
        )
