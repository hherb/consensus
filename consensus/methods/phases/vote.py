"""Vote phase handler for Voting Method.

Each participant votes on all pending motions (for / against / abstain).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ..base import Phase, ProcessedResponse
from ..phase_handler import PhaseHandler
from ._voting_helpers import (
    VALID_VOTES,
    extract_votes,
    format_motions_for_voting,
)

if TYPE_CHECKING:
    from ...models import Discussion, Entity

logger = logging.getLogger(__name__)


class VoteHandler(PhaseHandler):
    """Phase 2: Formal voting on pending motions."""

    phase = Phase(
        name="vote",
        display_name="Voting",
        description=(
            "Vote on each pending motion: for, against, or abstain.  "
            "Include reasoning with your vote."
        ),
        rounds=1,
    )

    # ------------------------------------------------------------------
    # Prompts
    # ------------------------------------------------------------------

    def get_system_prompt(self, entity: Entity,
                          discussion: Discussion) -> str:
        state = discussion.method_state
        motions_text = format_motions_for_voting(state)
        return (
            f"You are {entity.name}, participating in a structured "
            f"deliberation with voting.\n"
            f"Topic: {discussion.topic}\n\n"
            "VOTING PHASE\n\n"
            "Vote on each motion below.  For EACH motion, include a "
            "JSON block with your vote:\n"
            "```json\n"
            '{"vote": "for|against|abstain", "motion_id": <number>}\n'
            "```\n\n"
            "Valid votes: 'for', 'against', 'abstain'\n\n"
            "Provide reasoning for each vote after the JSON block.\n\n"
            f"Motions to vote on:\n{motions_text}"
        )

    def get_turn_prompt(self, entity: Entity,
                        discussion: Discussion) -> str:
        state = discussion.method_state
        motions = state.get("motions", [])
        votes = state.get("votes", [])
        voted_motions = {v["motion_id"] for v in votes
                        if v["entity_id"] == entity.id}
        unvoted = [m for m in motions if m["id"] not in voted_motions]

        if not unvoted:
            return f"{entity.name}, you have already voted on all motions."

        motion_list = "\n".join(
            f"  Motion {m['id']}: {m['text']}" for m in unvoted
        )
        return (
            f"{entity.name}, please cast your vote on each motion.\n\n"
            "CRITICAL: For EACH motion, include a JSON block as the "
            "FIRST thing, before your reasoning:\n"
            "```json\n"
            '{"vote": "for|against|abstain", "motion_id": <number>}\n'
            "```\n\n"
            f"Motions awaiting your vote:\n{motion_list}"
        )

    def get_summary_prompt(self, discussion: Discussion,
                           speaker_name: str,
                           next_speaker_name: str) -> str:
        return (
            f"{speaker_name} has cast their vote(s).  "
            f"Invite {next_speaker_name} to vote."
        )

    # ------------------------------------------------------------------
    # Response processing
    # ------------------------------------------------------------------

    def process_response(self, content: str, entity: Entity,
                         discussion: Discussion) -> ProcessedResponse:
        state = discussion.method_state
        votes = extract_votes(content)
        valid_motion_ids = {m["id"] for m in state.get("motions", [])}
        accepted = 0

        for vote_data in votes:
            vote_val = vote_data.get("vote", "").lower()
            motion_id = vote_data.get("motion_id")

            if vote_val not in VALID_VOTES:
                logger.warning(
                    "Invalid vote value '%s' from %s, skipping",
                    vote_val, entity.name,
                )
                continue
            if motion_id not in valid_motion_ids:
                logger.warning(
                    "Vote for unknown motion %s from %s, skipping",
                    motion_id, entity.name,
                )
                continue
            # Prevent double-voting
            already_voted = any(
                v["entity_id"] == entity.id and v["motion_id"] == motion_id
                for v in state.get("votes", [])
            )
            if already_voted:
                logger.info(
                    "%s already voted on motion %d, skipping duplicate",
                    entity.name, motion_id,
                )
                continue

            state.setdefault("votes", []).append({
                "entity_id": entity.id,
                "entity_name": entity.name,
                "motion_id": motion_id,
                "vote": vote_val,
                "rationale": vote_data.get("rationale", ""),
            })
            accepted += 1

        extracted = {"votes_cast": accepted}
        if accepted:
            content += f"\n\n---\n**Votes cast:** {accepted}"

        return ProcessedResponse(
            display_content=content,
            extracted_data=extracted,
        )

    # ------------------------------------------------------------------
    # Phase advancement
    # ------------------------------------------------------------------

    def should_advance(self, discussion: Discussion) -> bool:
        """Advance when all participants have voted on all motions."""
        return _all_votes_in(discussion)

    # ------------------------------------------------------------------
    # Transition message (when transitioning TO this phase)
    # ------------------------------------------------------------------

    def get_transition_message(self, discussion: Discussion) -> str:
        state = discussion.method_state
        motions = state.get("motions", [])
        if not motions:
            return (
                f"**Phase: {self.phase.display_name}**\n\n"
                "No motions were proposed during deliberation.  "
                "The moderator will synthesise the discussion."
            )
        motions_text = format_motions_for_voting(state)
        return (
            f"**Phase: {self.phase.display_name}**\n\n"
            f"Deliberation is complete.  {len(motions)} motion(s) are "
            f"on the table:\n{motions_text}\n\n"
            "Each participant will now vote on every motion "
            "(for / against / abstain) with reasoning."
        )


def _all_votes_in(discussion: Discussion) -> bool:
    """Check if all participants have voted on all motions."""
    state = discussion.method_state
    motions = state.get("motions", [])
    votes = state.get("votes", [])

    if not motions:
        return False

    participant_ids = set(discussion.turn_order)

    for motion in motions:
        mid = motion["id"]
        voters = {v["entity_id"] for v in votes if v["motion_id"] == mid}
        if not participant_ids.issubset(voters):
            return False

    return True
