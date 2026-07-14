"""Beam-pruning phase handler for Tree of Thoughts (issue #26).

A moderator-only presentational phase (the ``rank_ideas.py`` /
``analyse_sensitivity.py`` pattern): the beam — the top ``BEAM_WIDTH``
thoughts by mean composite — is computed deterministically in
``_tot_helpers``, never by the model; the moderator's turn narrates
the cut.  Routing happens in ``next_phase`` (the issue-#22 hook):

- **converged** — the new *ordered* beam equals the previous pass's
  ordered beam and every survivor was freshly re-scored during the
  pass (eligibility restricts scoring to the previous beam, so the id
  set is vacuously stable after the first prune; order is the only
  movement re-scoring can produce, and partial coverage would let
  stale scores decide) → jump to ``synthesise``;
- **degenerate** — fewer than ``MIN_BEAM_SIZE`` survivors (nothing to
  explore in parallel) → jump to ``synthesise``;
- **depth budget** — ``MAX_TOT_DEPTH`` prune passes done → jump to
  ``synthesise``;
- otherwise → linear to ``expand`` (which loops back to ``score``).

The beam record and, when stopping, the final ``tot_artifact`` are
written inside ``next_phase`` — the one hook that runs exactly once
per pass.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ..base import LINEAR_NEXT, Phase, ProcessedResponse
from ..phase_handler import PhaseHandler
from ._delphi_helpers import anonymise_content
from ._tot_analysis import build_tot_artifact, compute_beam, format_ranking
from ._tot_helpers import (
    BEAM_WIDTH,
    MAX_TOT_DEPTH,
    MIN_BEAM_SIZE,
    STOP_CONVERGED,
    STOP_DEGENERATE,
    STOP_DEPTH,
    current_depth,
    thought_label,
)

if TYPE_CHECKING:
    from ...models import Discussion, Entity

logger = logging.getLogger(__name__)


class PruneThoughtsHandler(PhaseHandler):
    """Phase 3: The moderator presents the deterministic beam cut."""

    phase = Phase(
        name="prune",
        display_name="Beam Pruning",
        description=(
            "The scores are aggregated deterministically and only the "
            "strongest approaches survive.  The moderator presents the "
            "ranking and the cut."
        ),
        rounds=1,
    )

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    def init_state(self, discussion: Discussion) -> dict:
        return {"beam_history": [], "tot_artifact": {}}

    # ------------------------------------------------------------------
    # Turn order — moderator only
    # ------------------------------------------------------------------

    def get_turn_order(self, entity_ids: list[int],
                       discussion: Discussion) -> list[int]:
        """Only the moderator speaks during pruning."""
        return [discussion.moderator_id]

    # ------------------------------------------------------------------
    # Prompts (pure reads — the beam is recorded in next_phase)
    # ------------------------------------------------------------------

    def get_system_prompt(self, entity: Entity,
                          discussion: Discussion) -> str:
        state = discussion.method_state
        beam_ids, ranking = compute_beam(state)
        labels = ", ".join(thought_label(tid) for tid in beam_ids)
        return (
            "You are the moderator of a Tree of Thoughts session.\n"
            f"Topic: {discussion.topic}\n\n"
            "BEAM PRUNING PHASE\n\n"
            "The composite ranking below was computed deterministically "
            "from the participants' scores (feasibility + impact + "
            "inverted risk, averaged over scorers).  Do not alter it.\n\n"
            f"Ranking:\n{format_ranking(state, ranking)}\n\n"
            f"The top {BEAM_WIDTH} approaches survive the cut: {labels}."
            "\n\nPresent the outcome to the group: which approaches "
            "survive, what the scores say about why, and what stands "
            "out about the ones eliminated.  Keep it factual — quote "
            "the composites above."
        )

    # ------------------------------------------------------------------
    # Context filtering — anonymise authorship (whole-method blindness)
    # ------------------------------------------------------------------

    def filter_context_message(self, entity_name: str, content: str,
                               role: str,
                               discussion: Discussion, *,
                               current_entity_id: int | None = None) -> str:
        """The cut is presented on content, not authorship."""
        return anonymise_content(content, discussion)

    def get_turn_prompt(self, entity: Entity,
                        discussion: Discussion) -> str:
        return (
            "Present the ranking and the surviving beam to the group, "
            "quoting the computed composites."
        )

    # ------------------------------------------------------------------
    # Response processing — presentational, nothing to extract
    # ------------------------------------------------------------------

    def process_response(self, content: str, entity: Entity,
                         discussion: Discussion) -> ProcessedResponse:
        return ProcessedResponse(display_content=content)

    # ------------------------------------------------------------------
    # Phase advancement & routing
    # ------------------------------------------------------------------

    def next_phase(self, discussion: Discussion) -> str | None:
        """Record the beam and route: loop onward or stop to synthesise."""
        state = discussion.method_state
        beam_ids, ranking = compute_beam(state)
        # Labels freshly scored during the pass that just ended carry
        # this pre-append depth tag (see record_thought_scores).
        fresh_labels = set(state.get("scores_by_pass", {}).get(
            str(current_depth(state)), []))
        history = state.setdefault("beam_history", [])
        prev = history[-1]["beam_ids"] if history else None
        history.append({"depth": current_depth(state) + 1,
                        "beam_ids": beam_ids, "ranking": ranking})
        # An unchanged ordered beam only counts as convergence when the
        # pass freshly re-scored EVERY survivor — stability under zero
        # or partial new data (extractions failed, humans skipped)
        # would let stale earlier-pass scores decide the outcome.
        converged = (prev is not None and prev == beam_ids
                     and {thought_label(tid) for tid in prev}
                     <= fresh_labels)
        degenerate = len(beam_ids) < MIN_BEAM_SIZE
        depth_spent = current_depth(state) >= MAX_TOT_DEPTH
        if converged or degenerate or depth_spent:
            reason = (STOP_CONVERGED if converged
                      else STOP_DEGENERATE if degenerate else STOP_DEPTH)
            state["tot_artifact"] = build_tot_artifact(state, reason)
            logger.info(
                "Tree of Thoughts stopping after prune pass %d (%s) — "
                "moving to synthesis", current_depth(state), reason)
            return "synthesise"
        logger.info(
            "Prune pass %d kept beam %s — continuing to expansion",
            current_depth(state), beam_ids)
        return LINEAR_NEXT  # → expand

    # ------------------------------------------------------------------
    # Transition message (when transitioning TO this phase)
    # ------------------------------------------------------------------

    def get_transition_message(self, discussion: Discussion) -> str:
        """Embed the computed ranking in the transcript.

        The system prompt is only rendered for AI moderators; putting
        the deterministic ranking in the transition message keeps a
        HUMAN moderator (and the group) looking at the same numbers
        the beam cut is recorded from (the ``rank_ideas.py`` /
        ``analyse_sensitivity.py`` precedent).
        """
        state = discussion.method_state
        return (
            f"**Phase: {self.phase.display_name}**\n\n"
            "Scoring is complete.  Computed ranking (feasibility + "
            "impact + inverted risk, averaged over scorers):\n"
            f"{format_ranking(state)}\n\n"
            f"The top {BEAM_WIDTH} approaches survive the prune; the "
            "moderator will present the cut."
        )
