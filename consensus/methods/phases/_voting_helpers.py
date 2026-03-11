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
    from ...models import Discussion

logger = logging.getLogger(__name__)

# Valid vote values
VALID_VOTES = {"for", "against", "abstain"}

# Default deliberation rounds before voting
DEFAULT_DELIBERATION_ROUNDS = 2


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
