"""Shared helpers for Voting Method phase handlers.

Contains motion/vote extraction, tallying, and formatting — used by
the deliberate, vote, and tally phase handlers.
"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...models import Discussion, Entity

logger = logging.getLogger(__name__)

# Valid vote values
VALID_VOTES = {"for", "against", "abstain"}

# Default deliberation rounds before voting
DEFAULT_DELIBERATION_ROUNDS = 2

#: JSON Schema for the submit_votes output tool (issue #23).
VOTES_TOOL_PARAMETERS: dict = {
    "type": "object",
    "properties": {
        "votes": {
            "type": "array",
            "description": "One entry per motion you are voting on.",
            "items": {
                "type": "object",
                "properties": {
                    "motion_id": {"type": "integer"},
                    "vote": {"type": "string",
                             "enum": ["for", "against", "abstain"]},
                    "rationale": {"type": "string"},
                },
                "required": ["motion_id", "vote", "rationale"],
            },
        },
    },
    "required": ["votes"],
}


def record_votes(state: dict, entity: Entity, votes: list[dict]) -> int:
    """Validate, dedupe, and append votes to state; return count accepted.

    Shared by the free-text and structured-output paths (issue #23).
    """
    valid_motion_ids = {m["id"] for m in state.get("motions", [])}
    accepted = 0

    for vote_data in votes:
        # str(... or "") guards non-string JSON values (null, 1, ...)
        vote_val = str(vote_data.get("vote") or "").lower()
        motion_id = vote_data.get("motion_id")

        if vote_val not in VALID_VOTES:
            logger.warning(
                "Invalid vote value '%s' from %s, skipping",
                vote_val, entity.name,
            )
            continue
        # Motion ids are stored as ints; models sometimes emit them as
        # JSON strings ("1").  Coerce so the membership test below does
        # not silently drop an otherwise-valid vote.
        try:
            motion_id = int(motion_id)
        except (TypeError, ValueError):
            logger.warning(
                "Vote with non-numeric motion_id %r from %s, skipping",
                motion_id, entity.name,
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

    return accepted


def extract_motions(content: str) -> list[str]:
    """Parse motion proposals from JSON blocks in the response."""
    motions: list[str] = []

    for match in re.finditer(r'```(?:json)?\s*(\{[^`]+\})\s*```',
                              content, re.DOTALL):
        try:
            data = json.loads(match.group(1))
            if "motion" in data and isinstance(data["motion"], str):
                motions.append(data["motion"].strip())
        except (json.JSONDecodeError, TypeError):
            continue

    return motions


def extract_votes(content: str) -> list[dict]:
    """Parse vote JSON blocks from the response."""
    votes: list[dict] = []

    for match in re.finditer(r'```(?:json)?\s*(\{[^`]+\})\s*```',
                              content, re.DOTALL):
        try:
            data = json.loads(match.group(1))
            if "vote" in data and "motion_id" in data:
                votes.append(data)
        except (json.JSONDecodeError, TypeError):
            continue

    return votes


def tally_votes(discussion: Discussion) -> dict[int, dict[str, int]]:
    """Tally votes per motion.

    Returns:
        Dict mapping motion_id -> {"for": N, "against": N, "abstain": N}.
    """
    state = discussion.method_state
    motions = state.get("motions", [])
    votes = state.get("votes", [])

    tally: dict[int, dict[str, int]] = {}
    for motion in motions:
        mid = motion["id"]
        tally[mid] = {"for": 0, "against": 0, "abstain": 0}

    for vote in votes:
        mid = vote["motion_id"]
        val = vote["vote"]
        if mid in tally and val in tally[mid]:
            tally[mid][val] += 1

    return tally


def format_motions(state: dict) -> str:
    """Format current motions for display."""
    motions = state.get("motions", [])
    if not motions:
        return "  (No motions proposed yet)"
    return "\n".join(
        f"  Motion {m['id']}: \"{m['text']}\" (proposed by {m['proposed_by']})"
        for m in motions
    )


def format_motions_for_voting(state: dict) -> str:
    """Format motions with IDs for the voting phase."""
    motions = state.get("motions", [])
    if not motions:
        return "  (No motions to vote on)"
    return "\n".join(
        f"  Motion {m['id']}: \"{m['text']}\""
        for m in motions
    )


def format_tally(tally: dict[int, dict[str, int]],
                 motions: list[dict],
                 discussion: Discussion) -> str:
    """Format vote tally for display."""
    n_voters = len(discussion.turn_order)
    threshold = discussion.method_state.get("threshold", "simple_majority")

    lines = []
    for motion in motions:
        mid = motion["id"]
        counts = tally.get(mid, {"for": 0, "against": 0, "abstain": 0})
        total = counts["for"] + counts["against"]

        if threshold == "unanimous":
            passed = counts["against"] == 0 and counts["for"] > 0
        elif threshold == "supermajority":
            passed = total > 0 and counts["for"] / total >= 2 / 3
        else:  # simple_majority
            passed = counts["for"] > counts["against"]

        status = "PASSED" if passed else "FAILED"

        lines.append(
            f"  Motion {mid}: \"{motion['text']}\"\n"
            f"    For: {counts['for']}  |  Against: {counts['against']}  |  "
            f"Abstain: {counts['abstain']}  →  **{status}**"
        )

    return "\n".join(lines)
