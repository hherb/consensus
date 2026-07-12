"""Shared helpers for Belief Diffusion phase handlers.

Contains belief extraction, formatting, convergence checking, and
trajectory summaries — used by multiple Belief Diffusion phases.
"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...models import Discussion, Entity

logger = logging.getLogger(__name__)

# Convergence: stop when the maximum belief shift across all
# participants and hypotheses falls below this threshold.
DEFAULT_CONVERGENCE_THRESHOLD = 0.05

# Safety limit for diffusion rounds
MAX_DIFFUSE_ROUNDS = 10

# Width of the belief bar chart in characters
BELIEF_BAR_WIDTH = 30

# Minimum character length for a parsed hypothesis to be kept
MIN_HYPOTHESIS_LENGTH = 5

# Acceptable hypothesis-count bounds for structured framing (the
# prompt asks for 3-5; validation is slightly tolerant).
MIN_HYPOTHESES = 2
MAX_HYPOTHESES = 6

#: JSON Schema for the submit_hypotheses output tool (issue #23).
HYPOTHESES_TOOL_PARAMETERS: dict = {
    "type": "object",
    "properties": {
        "hypotheses": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 3,
            "maxItems": 5,
            "description": ("3-5 competing, mutually exclusive "
                            "hypotheses that together cover the "
                            "plausible answer space."),
        },
        "rationale": {
            "type": "string",
            "description": ("Brief explanation of how the hypotheses "
                            "partition the answer space."),
        },
    },
    "required": ["hypotheses"],
}


def extract_beliefs(content: str) -> dict[str, float]:
    """Parse a belief JSON block from the response content."""
    # Try to find ```json ... ``` block
    json_match = re.search(r'```(?:json)?\s*(\{[^`]+\})\s*```', content,
                           re.DOTALL)
    if json_match:
        try:
            data = json.loads(json_match.group(1))
            if "beliefs" in data and isinstance(data["beliefs"], dict):
                return {k: float(v) for k, v in data["beliefs"].items()}
        except (json.JSONDecodeError, ValueError, TypeError):
            pass

    # Fallback: look for inline JSON object with "beliefs" key
    belief_match = re.search(
        r'\{"beliefs"\s*:\s*\{[^}]+\}\s*\}', content)
    if belief_match:
        try:
            data = json.loads(belief_match.group(0))
            return {k: float(v) for k, v in data["beliefs"].items()}
        except (json.JSONDecodeError, ValueError, TypeError):
            pass

    return {}


def format_belief_bar(beliefs: dict[str, float],
                      discussion: Discussion) -> str:
    """Format beliefs as a visual bar chart in markdown."""
    hypotheses = discussion.method_state.get("hypotheses", [])
    lines = ["**Belief Distribution:**"]
    for key, prob in sorted(beliefs.items()):
        # Map H1 -> hypothesis text
        try:
            idx = int(key.lstrip("H")) - 1 if key.startswith("H") else -1
        except ValueError:
            idx = -1
        label = hypotheses[idx] if 0 <= idx < len(hypotheses) else key
        bar_len = int(prob * BELIEF_BAR_WIDTH)
        bar = "\u2588" * bar_len + "\u2591" * (BELIEF_BAR_WIDTH - bar_len)
        lines.append(f"  {key} ({prob:.0%}) {bar}  *{label}*")
    return "\n".join(lines)


def format_others_beliefs(entity: Entity,
                          discussion: Discussion) -> str:
    """Format other participants' latest beliefs for context."""
    state = discussion.method_state
    history = state.get("belief_history", [])

    if not history:
        return "(No beliefs recorded yet)"

    # Find the latest entry per entity (excluding current entity)
    latest: dict[int, dict] = {}
    for entry in history:
        eid = entry["entity_id"]
        if eid != entity.id:
            latest[eid] = entry

    if not latest:
        return "(No other participants have stated beliefs yet)"

    lines = []
    for eid, entry in latest.items():
        name = entry.get("entity_name", f"Entity {eid}")
        beliefs = entry.get("beliefs", {})
        probs = ", ".join(f"{k}: {v:.0%}" for k, v in
                          sorted(beliefs.items()))
        lines.append(f"  {name}: [{probs}]")
    return "\n".join(lines)


def build_trajectory_summary(discussion: Discussion) -> str:
    """Build a text summary of belief trajectories over all rounds."""
    state = discussion.method_state
    history = state.get("belief_history", [])

    if not history:
        return "(No data)"

    # Group by entity
    by_entity: dict[int, list[dict]] = {}
    for entry in history:
        by_entity.setdefault(entry["entity_id"], []).append(entry)

    lines = []
    for eid, entries in by_entity.items():
        name = entries[0].get("entity_name", f"Entity {eid}")
        lines.append(f"  **{name}:**")
        for entry in sorted(entries, key=lambda e: e["round"]):
            probs = ", ".join(f"{k}: {v:.0%}" for k, v in
                              sorted(entry["beliefs"].items()))
            label = "Prior" if entry["round"] == 0 else f"Round {entry['round']}"
            lines.append(f"    {label}: [{probs}]")

    return "\n".join(lines)


def extract_hypotheses_from_framing(content: str) -> list[str]:
    """Parse hypotheses from the moderator's framing message.

    Looks for numbered lists (1. ... 2. ...) or H1: ... H2: ... patterns.
    """
    # Pattern 1: numbered list
    numbered = re.findall(r'^\s*(?:H?\d+[\.\):])\s*(.+)', content,
                          re.MULTILINE)
    if numbered:
        return [h.strip().rstrip('.') for h in numbered
                if len(h.strip()) > MIN_HYPOTHESIS_LENGTH]

    # Pattern 2: bold or markdown list items
    bullets = re.findall(r'^\s*[-*]\s*\*?\*?(.+?)\*?\*?\s*$', content,
                         re.MULTILINE)
    if bullets:
        return [h.strip().rstrip('.') for h in bullets
                if len(h.strip()) > MIN_HYPOTHESIS_LENGTH]

    return []


def check_convergence(discussion: Discussion) -> bool:
    """Check if beliefs have converged below threshold."""
    state = discussion.method_state
    history = state.get("belief_history", [])
    threshold = state.get("convergence_threshold",
                          DEFAULT_CONVERGENCE_THRESHOLD)

    if len(history) < 2:
        return False

    # Get the last two rounds
    current_round = state.get("diffuse_round", 0)
    if current_round < 2:
        return False

    current_beliefs = {}
    prev_beliefs = {}
    for entry in history:
        if entry["round"] == current_round:
            current_beliefs[entry["entity_id"]] = entry["beliefs"]
        elif entry["round"] == current_round - 1:
            prev_beliefs[entry["entity_id"]] = entry["beliefs"]

    if not current_beliefs or not prev_beliefs:
        return False

    # Max delta across all participants and hypotheses
    max_delta = 0.0
    for eid, beliefs in current_beliefs.items():
        prev = prev_beliefs.get(eid, {})
        for hyp, prob in beliefs.items():
            old_prob = prev.get(hyp, 0.0)
            max_delta = max(max_delta, abs(prob - old_prob))

    converged = max_delta < threshold
    if converged:
        logger.info(
            "Belief diffusion converged (max_delta=%.4f < threshold=%.4f)",
            max_delta, threshold,
        )
    return converged
