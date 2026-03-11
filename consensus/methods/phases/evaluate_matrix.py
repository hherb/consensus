"""Matrix evaluation phase handler for Analysis of Competing Hypotheses.

Each participant rates every hypothesis against every piece of evidence
using +/-/0 ratings.  Ratings are parsed from JSON and stored in the
discussion's method state.
"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING

from ..base import Phase, ProcessedResponse
from ..parsing import extract_json_block
from ..phase_handler import PhaseHandler

if TYPE_CHECKING:
    from ...models import Discussion, Entity

logger = logging.getLogger(__name__)


class EvaluateMatrixHandler(PhaseHandler):
    """Phase 3: Rate hypotheses against evidence."""

    phase = Phase(
        name="evaluate",
        display_name="Matrix Evaluation",
        description=(
            "Rate each hypothesis against each piece of evidence.  "
            "Focus especially on INCONSISTENCIES — evidence that "
            "contradicts a hypothesis is more diagnostic than evidence "
            "that supports it."
        ),
        rounds=1,
    )

    # ------------------------------------------------------------------
    # Prompts
    # ------------------------------------------------------------------

    def get_system_prompt(self, entity: Entity,
                          discussion: Discussion) -> str:
        state = discussion.method_state
        hypotheses = state.get("hypotheses", [])
        evidence = state.get("evidence", [])

        base = (
            f"You are {entity.name}, participating in an Analysis of "
            f"Competing Hypotheses (ACH) structured analysis.\n"
            f"Topic: {discussion.topic}\n\n"
        )

        hyp_list = "\n".join(f"  H{i+1}: {h}"
                             for i, h in enumerate(hypotheses))
        ev_list = "\n".join(
            f"  E{e['id']}: {e['text']} (Source: {e.get('source', '?')})"
            for e in evidence
        )
        return base + (
            "MATRIX EVALUATION PHASE\n\n"
            f"Hypotheses:\n{hyp_list}\n\n"
            f"Evidence:\n{ev_list}\n\n"
            "Rate EACH hypothesis against EACH piece of evidence.\n\n"
            "Use these ratings:\n"
            "  + (consistent) — the evidence is what you'd expect if "
            "this hypothesis were true\n"
            "  - (inconsistent) — the evidence contradicts or is unlikely "
            "under this hypothesis\n"
            "  0 (neutral) — the evidence doesn't meaningfully "
            "differentiate\n\n"
            "Output your ratings as a JSON matrix:\n"
            "```json\n"
            '{"ratings": {"H1": {"E1": "+", "E2": "-", ...}, '
            '"H2": {"E1": "0", ...}, ...}}\n'
            "```\n\n"
            "After the matrix, briefly explain your most important "
            "inconsistency ratings — why does that evidence contradict "
            "that hypothesis?"
        )

    def get_turn_prompt(self, entity: Entity,
                        discussion: Discussion) -> str:
        return (
            f"{entity.name}, evaluate each hypothesis against each piece "
            "of evidence using the +/-/0 rating system.  Include the "
            "JSON matrix."
        )

    def get_summary_prompt(self, discussion: Discussion,
                           speaker_name: str,
                           next_speaker_name: str) -> str:
        return (
            f"{speaker_name} has submitted their evaluation matrix.  "
            "Note any ratings that differ significantly from previous "
            f"evaluators.  Next: {next_speaker_name}."
        )

    # ------------------------------------------------------------------
    # Response processing
    # ------------------------------------------------------------------

    def process_response(self, content: str, entity: Entity,
                         discussion: Discussion) -> ProcessedResponse:
        state = discussion.method_state
        ratings = self._parse_ratings(content)

        if ratings:
            state.setdefault("matrix", {})[str(entity.id)] = ratings

            # Augment display with formatted matrix
            matrix_text = self._format_rating_matrix(ratings, discussion)
            display = f"{content}\n\n---\n{matrix_text}"
        else:
            logger.warning(
                "Could not extract ratings from %s's evaluation",
                entity.name,
            )
            display = content

        return ProcessedResponse(
            display_content=display,
            extracted_data={"ratings": ratings},
        )

    # ------------------------------------------------------------------
    # Phase advancement
    # ------------------------------------------------------------------

    def should_advance(self, discussion: Discussion) -> bool:
        return discussion.method_state.get("phase_round", 1) > 1

    # ------------------------------------------------------------------
    # Transition message
    # ------------------------------------------------------------------

    def get_transition_message(self, discussion: Discussion) -> str:
        state = discussion.method_state
        evidence = state.get("evidence", [])
        return (
            f"**Phase: {self.phase.display_name}**\n\n"
            f"{len(evidence)} pieces of evidence have been gathered.  "
            "Each participant will now rate every hypothesis against "
            "every piece of evidence using +/-/0 ratings."
        )

    # ------------------------------------------------------------------
    # Parsing helpers (ACH-specific)
    # ------------------------------------------------------------------

    def _parse_ratings(self, content: str) -> dict[str, dict[str, str]]:
        """Extract the rating matrix JSON from content."""
        # Try ```json block via shared utility
        data = extract_json_block(content)
        if isinstance(data, dict) and "ratings" in data:
            return data["ratings"]

        # Fallback: inline JSON
        match = re.search(r'\{"ratings"\s*:\s*\{.+?\}\s*\}', content,
                          re.DOTALL)
        if match:
            try:
                data = json.loads(match.group(0))
                return data.get("ratings", {})
            except (json.JSONDecodeError, ValueError):
                pass

        return {}

    def _format_rating_matrix(self, ratings: dict,
                              discussion: Discussion) -> str:
        """Format a single evaluator's rating matrix as markdown."""
        hypotheses = discussion.method_state.get("hypotheses", [])
        evidence = discussion.method_state.get("evidence", [])

        if not ratings or not evidence:
            return ""

        lines = ["**Rating Matrix:**", ""]

        # Header
        e_headers = " | ".join(f"E{e['id']}" for e in evidence)
        lines.append(f"| | {e_headers} |")
        lines.append(f"|---|{'---|' * len(evidence)}")

        # Rows
        for hi, h in enumerate(hypotheses):
            key = f"H{hi+1}"
            h_ratings = ratings.get(key, {})
            cells = []
            for e in evidence:
                ekey = f"E{e['id']}"
                r = h_ratings.get(ekey, "?")
                cells.append(r)
            lines.append(f"| **{key}** | {' | '.join(cells)} |")

        return "\n".join(lines)
