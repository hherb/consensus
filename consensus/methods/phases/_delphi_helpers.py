"""Shared helpers for Delphi Method phase handlers.

Contains estimate extraction, anonymisation mapping, convergence
checking, and distribution summary — used by multiple Delphi phases.
"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...models import Discussion

logger = logging.getLogger(__name__)

# Convergence: stop when the IQR (inter-quartile range) of numeric
# estimates falls below this fraction of the median.
DEFAULT_CONVERGENCE_RATIO = 0.15

# Maximum revision rounds
MAX_REVISE_ROUNDS = 5


def extract_estimate(content: str) -> dict:
    """Parse an estimate JSON block from the response."""
    # Try ```json block
    json_match = re.search(r'```(?:json)?\s*(\{[^`]+\})\s*```', content,
                           re.DOTALL)
    if json_match:
        try:
            data = json.loads(json_match.group(1))
            if "estimate" in data:
                data["estimate"] = float(data["estimate"])
                return data
        except (json.JSONDecodeError, ValueError, TypeError):
            pass

    # Fallback: inline JSON with "estimate" key
    match = re.search(r'\{"estimate"\s*:.+?\}', content, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group(0))
            data["estimate"] = float(data["estimate"])
            return data
        except (json.JSONDecodeError, ValueError, TypeError):
            pass

    # Last resort: find "estimate" or "Estimate" followed by a number
    # in natural language (e.g. "my estimate is 0.85" or "Estimate: 0.90")
    num_match = re.search(
        r'(?:my\s+)?(?:revised?\s+)?estimate[:\s]+(?:is\s+)?'
        r'(\d+(?:\.\d+)?)',
        content, re.IGNORECASE,
    )
    if num_match:
        try:
            value = float(num_match.group(1))
            logger.info(
                "Extracted estimate %.4g from natural language fallback",
                value,
            )
            return {"estimate": value, "confidence": "", "unit": ""}
        except (ValueError, TypeError):
            pass

    return {}


def build_panelist_map(discussion: Discussion) -> dict[str, str]:
    """Build a stable name -> panelist mapping from entity order.

    Stored in method_state so it's consistent across calls.
    """
    state = discussion.method_state
    panelist_map = state.get("_panelist_map")
    if not panelist_map:
        panelist_map = {}
        mod_id = discussion.moderator_id
        idx = 1
        for e in discussion.entities:
            if e.id != mod_id:
                panelist_map[e.name] = f"Panelist {idx}"
                idx += 1
        state["_panelist_map"] = panelist_map
    return panelist_map


def anonymise_content(content: str, discussion: Discussion) -> str:
    """Replace real names with 'Panelist N' labels."""
    panelist_map = build_panelist_map(discussion)
    for name, alias in panelist_map.items():
        content = content.replace(name, alias)
    return content


def check_convergence(discussion: Discussion) -> bool:
    """Check if estimates have converged."""
    state = discussion.method_state
    estimates = state.get("estimates", [])
    revise_round = state.get("revise_round", 0)
    threshold = state.get("convergence_ratio", DEFAULT_CONVERGENCE_RATIO)

    if revise_round < 1:
        return False

    # Get latest round's numeric estimates
    current_values = []
    for e in estimates:
        if e["round"] == revise_round and e.get("value") is not None:
            current_values.append(e["value"])

    if len(current_values) < 2:
        return False

    current_values.sort()
    # Use index-based median and quartiles (upper-middle for even
    # lengths) — sufficient for Delphi convergence detection where
    # approximate spread matters more than statistical precision.
    median = current_values[len(current_values) // 2]
    if median == 0:
        return False

    q1 = current_values[len(current_values) // 4]
    q3 = current_values[3 * len(current_values) // 4]
    iqr = q3 - q1
    ratio = abs(iqr / median)

    converged = ratio < threshold
    if converged:
        logger.info(
            "Delphi converged (IQR/median=%.4f < threshold=%.4f)",
            ratio, threshold,
        )
    return converged


def build_distribution_summary(discussion: Discussion) -> str:
    """Build an anonymised summary of the latest round's estimates."""
    state = discussion.method_state
    estimates = state.get("estimates", [])

    if not estimates:
        return "(No estimates yet)"

    # Find the latest round
    latest_round = max(e["round"] for e in estimates)
    latest = [e for e in estimates if e["round"] == latest_round]

    if not latest:
        return "(No estimates for this round)"

    values = [e["value"] for e in latest if e.get("value") is not None]
    if not values:
        return "(No numeric estimates extracted)"

    values.sort()
    n = len(values)
    mean = sum(values) / n
    median = values[n // 2]
    low = values[0]
    high = values[-1]
    q1 = values[n // 4] if n >= 4 else low
    q3 = values[3 * n // 4] if n >= 4 else high

    unit = latest[0].get("unit", "") if latest else ""

    lines = [
        f"  Participants: {n}",
        f"  Mean: {mean:.4g} {unit}",
        f"  Median: {median:.4g} {unit}",
        f"  Range: [{low:.4g}, {high:.4g}] {unit}",
        f"  Inter-quartile range: [{q1:.4g}, {q3:.4g}] {unit}",
        "",
        "  Individual estimates (anonymised, sorted):",
    ]
    # Sort entries by value for anonymised display, preserving
    # the correct confidence for each entry even when values match.
    sorted_entries = sorted(
        [e for e in latest if e.get("value") is not None],
        key=lambda e: e["value"],
    )
    for i, entry in enumerate(sorted_entries):
        v = entry["value"]
        conf = entry.get("confidence", "")
        lines.append(f"    Panelist {i+1}: {v:.4g} {unit} "
                     f"(Confidence: {conf})")

    return "\n".join(lines)
