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

#: Lower/upper bound for an individual belief probability.
BELIEF_MIN = 0.0
BELIEF_MAX = 1.0

#: Allowed deviation of a belief distribution's sum from 1.0 —
#: generous enough for two-decimal rounding across MAX_HYPOTHESES
#: values (e.g. 0.33 * 3 = 0.99) without accepting non-distributions.
BELIEF_SUM_TOLERANCE = 0.05

#: JSON Schema for the submit_beliefs output tool (issue #23).
#: Keys are hypothesis labels ("H1", "H2", ...) — the same convention
#: the free-text path and the display/convergence helpers use — so
#: structured and fallback turns stay comparable in belief_history.
BELIEFS_TOOL_PARAMETERS: dict = {
    "type": "object",
    "properties": {
        "beliefs": {
            "type": "object",
            "description": (
                "Map of every hypothesis label (H1, H2, ...) to your "
                "probability estimate for it (0.0-1.0). Include one entry "
                "per hypothesis label."
            ),
            "additionalProperties": {
                "type": "number",
                "minimum": BELIEF_MIN,
                "maximum": BELIEF_MAX,
            },
        },
        "reasoning": {
            "type": "string",
            "description": "Brief explanation of your probability assignments.",
        },
    },
    "required": ["beliefs"],
}


def hypothesis_labels(hypotheses: list[str]) -> list[str]:
    """Return the label set ('H1'..'Hn') for the framed hypotheses."""
    return [f"H{i}" for i in range(1, len(hypotheses) + 1)]


def format_labelled_hypotheses(hypotheses: list[str]) -> str:
    """Format hypotheses as a labelled list ('  H1: <text>' per line)."""
    return "\n".join(f"  H{i}: {h}" for i, h in enumerate(hypotheses, 1))


def validate_beliefs_payload(payload: dict, hypotheses: list[str]) -> str:
    """Return '' if a submit_beliefs payload is usable, else an error.

    The keys of ``beliefs`` must be exactly the label set
    ``H1..H{len(hypotheses)}`` (unknown or missing labels are named in
    the error), every value must be numeric in [0, 1], and the values
    must sum to 1.0 within ``BELIEF_SUM_TOLERANCE``.
    """
    beliefs = payload.get("beliefs")
    if not isinstance(beliefs, dict) or not beliefs:
        return ("'beliefs' must be a non-empty object mapping each "
                "hypothesis label (H1, H2, ...) to a probability.")

    valid = hypothesis_labels(hypotheses)
    unknown = [key for key in beliefs if key not in valid]
    if unknown:
        return (f"Unknown hypothesis label(s) {unknown}. "
                f"Valid labels: {valid}.")

    for key, value in beliefs.items():
        try:
            v = float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return (f"The belief for '{key}' must be a number between "
                    f"{BELIEF_MIN} and {BELIEF_MAX}.")
        if not (BELIEF_MIN <= v <= BELIEF_MAX):
            return (f"The belief for '{key}' must be between "
                    f"{BELIEF_MIN} and {BELIEF_MAX}.")

    missing = [label for label in valid if label not in beliefs]
    if missing:
        return (f"Provide a belief for every hypothesis label. "
                f"Missing: {missing}.")

    total = sum(float(v) for v in beliefs.values())  # type: ignore[arg-type]
    if abs(total - 1.0) > BELIEF_SUM_TOLERANCE:
        return (f"The beliefs must form a probability distribution "
                f"summing to 1.0 (got {total:.2f}).")

    return ""


def record_beliefs(state: dict, entity: Entity, round_num: int,
                   beliefs: dict[str, float]) -> None:
    """Append a beliefs entry to method_state['belief_history'].

    Shared by prior_beliefs.py and diffuse_beliefs.py to avoid
    duplicating the entry shape both handlers append.
    """
    state.setdefault("belief_history", []).append({
        "round": round_num,
        "entity_id": entity.id,
        "entity_name": entity.name,
        "beliefs": beliefs,
    })


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
