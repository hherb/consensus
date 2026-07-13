"""Clustering phase handler for Nominal Group Technique.

A moderator-only phase (see frame_hypotheses.py for the pattern): the
moderator merges duplicates and groups related raw ideas into a
deduplicated candidate list via the forced ``submit_candidates``
output tool (issue #23); free-text numbered-list parsing remains the
fallback path.  If no parseable clustering arrives after
``MAX_CLUSTER_ATTEMPTS``, the raw deduplicated ideas are promoted to
candidates 1:1 — voting on raw ideas beats ending the method.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ..base import LINEAR_NEXT, OutputToolSpec, Phase, ProcessedResponse
from ..parsing import parse_numbered_list
from ..phase_handler import PhaseHandler
from ._delphi_helpers import anonymise_content
from ._ngt_helpers import (
    CANDIDATES_TOOL_PARAMETERS,
    MAX_CLUSTER_ATTEMPTS,
    MIN_IDEA_LENGTH,
    fallback_candidates_from_ideas,
    format_candidates,
    format_ideas_for_clustering,
    record_candidates,
    validate_candidates_payload,
)

if TYPE_CHECKING:
    from ...models import Discussion, Entity

logger = logging.getLogger(__name__)


class ClusterIdeasHandler(PhaseHandler):
    """Phase 2: Moderator consolidates raw ideas into candidates."""

    phase = Phase(
        name="cluster",
        display_name="Clustering & Deduplication",
        description=(
            "The moderator merges duplicates and groups closely related "
            "ideas into a deduplicated list of candidate ideas."
        ),
        rounds=0,  # condition-based: candidates recorded or attempts exhausted
    )

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    def init_state(self, discussion: Discussion) -> dict:
        return {"candidates": [], "cluster_attempts": 0}

    # ------------------------------------------------------------------
    # Turn order — moderator only
    # ------------------------------------------------------------------

    def get_turn_order(self, entity_ids: list[int],
                       discussion: Discussion) -> list[int]:
        """Only the moderator speaks during clustering."""
        return [discussion.moderator_id]

    # ------------------------------------------------------------------
    # Prompts
    # ------------------------------------------------------------------

    def get_system_prompt(self, entity: Entity,
                          discussion: Discussion) -> str:
        ideas_text = format_ideas_for_clustering(discussion.method_state)
        return (
            "You are the moderator of a Nominal Group Technique (NGT) "
            "session, consolidating the ideas from silent generation.\n"
            f"Topic: {discussion.topic}\n\n"
            "CLUSTERING PHASE\n\n"
            "Merge duplicates and group closely related ideas into a "
            "single list of distinct candidate ideas.  Do NOT evaluate, "
            "rank, or drop substantive ideas — only consolidate.  "
            "Preserve minority and unconventional ideas as their own "
            f"candidates.\n\nRaw ideas (anonymised):\n{ideas_text}"
        )

    def get_turn_prompt(self, entity: Entity,
                        discussion: Discussion) -> str:
        state = discussion.method_state
        if state.get("cluster_attempts", 0) > 0:
            return (
                "The previous clustering was not usable.  Please call "
                "the submit_candidates tool with the consolidated "
                "candidate list — each candidate one complete, specific "
                "statement."
            )
        return (
            "Consolidate the raw ideas into a deduplicated candidate "
            "list by calling the submit_candidates tool.  Give each "
            "candidate a complete 'title' statement and an optional "
            "'summary' noting what was merged."
        )

    # ------------------------------------------------------------------
    # Context filtering — keep authorship hidden
    # ------------------------------------------------------------------

    def filter_context_message(self, entity_name: str, content: str,
                               role: str,
                               discussion: Discussion, *,
                               current_entity_id: int | None = None) -> str:
        return anonymise_content(content, discussion)

    # ------------------------------------------------------------------
    # Response processing (free-text / fallback path)
    # ------------------------------------------------------------------

    def process_response(self, content: str, entity: Entity,
                         discussion: Discussion) -> ProcessedResponse:
        state = discussion.method_state
        items = parse_numbered_list(content, min_length=MIN_IDEA_LENGTH)
        if items:
            record_candidates(state,
                              [{"title": t, "summary": ""} for t in items])
            logger.info("Extracted %d candidates from clustering",
                        len(items))
        else:
            state["cluster_attempts"] = state.get("cluster_attempts", 0) + 1
            logger.warning(
                "Clustering attempt %d failed — no candidates found",
                state["cluster_attempts"])
        return ProcessedResponse(display_content=content)

    # ------------------------------------------------------------------
    # Structured output (issue #23)
    # ------------------------------------------------------------------

    requires_structured_output = True

    def get_output_tool(self, entity: Entity,
                        discussion: Discussion) -> OutputToolSpec:
        return OutputToolSpec(
            name="submit_candidates",
            description=("Submit the consolidated, deduplicated candidate "
                         "list: an array of {title, summary} objects, plus "
                         "your reasoning."),
            parameters=CANDIDATES_TOOL_PARAMETERS,
        )

    def validate_output(self, payload: dict, entity: Entity,
                        discussion: Discussion) -> str:
        return validate_candidates_payload(payload)

    def process_structured_response(self, payload: dict, entity: Entity,
                                    discussion: Discussion) -> ProcessedResponse:
        state = discussion.method_state
        record_candidates(state, payload["candidates"])
        logger.info("Recorded %d candidates from structured clustering",
                    len(state["candidates"]))
        reasoning = str(payload.get("reasoning") or "").strip()
        listing = format_candidates(state)
        display = f"{reasoning}\n\n{listing}" if reasoning else listing
        return ProcessedResponse(display_content=display)

    # ------------------------------------------------------------------
    # Phase advancement
    # ------------------------------------------------------------------

    def should_advance(self, discussion: Discussion) -> bool:
        state = discussion.method_state
        if state.get("candidates"):
            return True
        if state.get("cluster_attempts", 0) >= MAX_CLUSTER_ATTEMPTS:
            logger.warning(
                "Giving up on clustering after %d attempts",
                MAX_CLUSTER_ATTEMPTS)
            return True
        return False

    def next_phase(self, discussion: Discussion) -> str | None:
        """Fall back to voting on raw ideas when clustering gave up.

        Aborts only in the (defensive) case that there are no ideas at
        all to promote — generation should already have ended the
        method in that situation.
        """
        state = discussion.method_state
        if not state.get("candidates"):
            fallback_candidates_from_ideas(state)
            if state.get("candidates"):
                logger.warning(
                    "Clustering gave up — promoting %d raw idea(s) to "
                    "candidates 1:1", len(state["candidates"]))
            else:
                logger.warning(
                    "Clustering ended with no ideas at all — ending the "
                    "NGT method early")
                return None
        return LINEAR_NEXT

    def get_method_complete_message(self, discussion: Discussion) -> str:
        state = discussion.method_state
        if state.get("candidates") or state.get("ideas"):
            return ""
        return (
            "⚠️ **Nominal Group Technique ended early.** No ideas were "
            "available to cluster, so the clarification and voting "
            "phases were skipped."
        )

    # ------------------------------------------------------------------
    # Transition message (when transitioning TO this phase)
    # ------------------------------------------------------------------

    def get_transition_message(self, discussion: Discussion) -> str:
        n = len(discussion.method_state.get("ideas", []))
        return (
            f"**Phase: {self.phase.display_name}**\n\n"
            f"Silent generation is complete — {n} idea(s) were "
            "collected.  The moderator will now merge duplicates and "
            "consolidate them into a candidate list for clarification "
            "and voting."
        )
