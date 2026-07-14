"""Candidate-scoring phase handler for Tree of Thoughts (issue #26).

Every participant scores every *eligible* thought (all thoughts on the
first pass, the surviving beam on later passes) on the method's three
fixed dimensions — feasibility, impact, risk — via the forced
``submit_thought_scores`` output tool (issue #23); a fenced/inline
JSON parse remains the human/fallback path.  Re-score passes show the
latest deep-dive expansions so updated scores are informed by them.
The composite, ranking, and beam are computed deterministically in
``_tot_helpers`` — never by the model.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ..base import OutputToolSpec, Phase, ProcessedResponse
from ..phase_handler import PhaseHandler
from ._tot_helpers import (
    DIMENSIONS,
    SCORE_MAX,
    SCORE_MIN,
    SCORES_TOOL_PARAMETERS,
    current_depth,
    eligible_thoughts,
    extract_json_payload,
    format_expansions,
    format_thoughts,
    record_thought_scores,
    thought_label,
    validate_scores_payload,
)

if TYPE_CHECKING:
    from ...models import Discussion, Entity

logger = logging.getLogger(__name__)


class ScoreThoughtsHandler(PhaseHandler):
    """Phase 2: Score every eligible thought on feasibility/impact/risk."""

    phase = Phase(
        name="score",
        display_name="Candidate Scoring",
        description=(
            "Each participant scores every surviving approach on "
            "feasibility, impact, and risk.  Scores drive the "
            "deterministic beam prune that follows."
        ),
        rounds=1,
    )

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    def init_state(self, discussion: Discussion) -> dict:
        return {"thought_scores": {}}

    # ------------------------------------------------------------------
    # Prompts
    # ------------------------------------------------------------------

    def _dimensions_blurb(self) -> str:
        """The shared scoring-scale explanation."""
        return (
            f"Score each approach from {SCORE_MIN} to {SCORE_MAX} on:\n"
            f"- feasibility ({SCORE_MIN}=infeasible, "
            f"{SCORE_MAX}=straightforward to execute)\n"
            f"- impact ({SCORE_MIN}=marginal, {SCORE_MAX}=transformative "
            "if it works)\n"
            f"- risk ({SCORE_MIN}=safe, {SCORE_MAX}=very likely to fail "
            "or backfire; high risk lowers the composite)"
        )

    def get_system_prompt(self, entity: Entity,
                          discussion: Discussion) -> str:
        state = discussion.method_state
        eligible = eligible_thoughts(state)
        base = (
            f"You are {entity.name}, participating in a Tree of Thoughts "
            "session.\n"
            f"Topic: {discussion.topic}\n\n"
            "SCORING PHASE\n\n"
        )
        if not eligible:
            return base + (
                "No candidate approaches are on the table.  Give a brief "
                "qualitative assessment of the topic instead."
            )
        depth = current_depth(state)
        parts = [base]
        if depth > 0:
            parts.append(
                f"This is re-score pass {depth + 1}: the beam was pruned "
                "and the survivors were deep-dived.  Re-score the "
                "surviving approaches in light of the refinements and "
                "obstacles below — updating your earlier scores is the "
                "point of this pass.\n\n"
                f"Latest deep-dives:\n{format_expansions(state, depth)}\n\n"
            )
        parts.append(
            f"Surviving approaches:\n{format_thoughts(eligible)}\n\n"
            f"{self._dimensions_blurb()}\n\n"
            "Submit your scores by calling the submit_thought_scores "
            "tool, mapping each approach label "
            f"({', '.join(thought_label(t['id']) for t in eligible)}) to "
            f"your {{{', '.join(DIMENSIONS)}}} integers, plus your "
            "reasoning — explain the extremes."
        )
        return "".join(parts)

    def get_turn_prompt(self, entity: Entity,
                        discussion: Discussion) -> str:
        eligible = eligible_thoughts(discussion.method_state)
        if not eligible:
            return (
                f"{entity.name}, no approaches could be scored — give a "
                "brief qualitative assessment instead."
            )
        return (
            f"{entity.name}, score every surviving approach on "
            f"{', '.join(DIMENSIONS)} ({SCORE_MIN}-{SCORE_MAX}) by "
            "calling the submit_thought_scores tool."
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
        scores = extract_json_payload(content, "scores")
        kept = (record_thought_scores(state, entity, scores)
                if isinstance(scores, dict) else 0)
        if kept:
            recorded = state["thought_scores"][str(entity.id)]
            table = self._format_entity_scores(recorded)
            display = f"{content}\n\n---\n{table}"
        else:
            logger.warning(
                "Could not extract thought scores from %s's response",
                entity.name)
            display = content
        return ProcessedResponse(display_content=display)

    @staticmethod
    def _format_entity_scores(recorded: dict[str, dict[str, int]]) -> str:
        """One participant's recorded scores as a compact table."""
        return "\n".join(
            f"  {label}: " + ", ".join(f"{dim} {values[dim]}"
                                       for dim in DIMENSIONS)
            for label, values in sorted(recorded.items()))

    # ------------------------------------------------------------------
    # Structured output (issue #23)
    # ------------------------------------------------------------------

    requires_structured_output = True

    def get_output_tool(self, entity: Entity,
                        discussion: Discussion) -> OutputToolSpec | None:
        """Declare the forced submit_thought_scores tool for this phase.

        Returns ``None`` when there is nothing to score (no eligible
        thoughts): no payload could pass validation, so forcing the
        tool would burn every retry (``score_options.py`` pattern).
        """
        eligible = eligible_thoughts(discussion.method_state)
        if not eligible:
            return None
        return OutputToolSpec(
            name="submit_thought_scores",
            description=(
                "Submit your feasibility/impact/risk scores "
                f"({SCORE_MIN}-{SCORE_MAX}) for each surviving approach, "
                "plus your reasoning.\n"
                f"Approaches:\n{format_thoughts(eligible)}"
            ),
            parameters=SCORES_TOOL_PARAMETERS,
        )

    def validate_output(self, payload: dict, entity: Entity,
                        discussion: Discussion) -> str:
        return validate_scores_payload(
            payload, eligible_thoughts(discussion.method_state))

    def process_structured_response(self, payload: dict, entity: Entity,
                                    discussion: Discussion) -> ProcessedResponse:
        """Store the submitted scores and render the score table.

        Writes ``state["thought_scores"][str(entity.id)]`` in exactly
        the shape the free-text path produces, so the beam computation
        works regardless of which path a turn took.
        """
        state = discussion.method_state
        record_thought_scores(state, entity, payload["scores"])
        recorded = state.get("thought_scores", {}).get(str(entity.id), {})
        table = self._format_entity_scores(recorded)
        reasoning = str(payload.get("reasoning") or "").strip()
        display = f"{reasoning}\n\n---\n{table}" if reasoning else table
        return ProcessedResponse(display_content=display)

    # ------------------------------------------------------------------
    # Transition message (when transitioning TO this phase)
    # ------------------------------------------------------------------

    def get_transition_message(self, discussion: Discussion) -> str:
        state = discussion.method_state
        depth = current_depth(state)
        eligible = eligible_thoughts(state)
        if depth == 0:
            return (
                f"**Phase: {self.phase.display_name}**\n\n"
                "The candidate approaches are in:\n"
                f"{format_thoughts(eligible)}\n\n"
                "Each participant will now score every approach on "
                f"{', '.join(DIMENSIONS)} ({SCORE_MIN}-{SCORE_MAX})."
            )
        return (
            f"**Phase: {self.phase.display_name} (re-score pass "
            f"{depth + 1})**\n\n"
            "The surviving approaches have been deep-dived:\n"
            f"{format_expansions(state, depth)}\n\n"
            "Each participant will now re-score the survivors in light "
            "of the refinements and obstacles."
        )
