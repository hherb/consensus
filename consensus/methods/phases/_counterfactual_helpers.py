"""Shared helpers for Counterfactual Stress Testing phase handlers.

Contains impact score extraction, claim classification, and results
table formatting — used by the stress test and synthesis phases.
"""

from __future__ import annotations

import re


def extract_impact_score(content: str) -> int | None:
    """Extract [IMPACT: N] tag from response content.

    Returns an integer 1-5, or None if no valid tag found.
    """
    match = re.search(r'\[IMPACT:\s*(\d)\s*\]', content)
    if not match:
        return None
    score = int(match.group(1))
    if score < 1 or score > 5:
        return None
    return score


def classify_claim(avg_score: float) -> str:
    """Classify a claim based on its average impact score.

    Returns LOAD-BEARING (>=4.0), SUPPORTIVE (>=2.0), or DECORATIVE (<2.0).
    """
    if avg_score >= 4.0:
        return "LOAD-BEARING"
    if avg_score >= 2.0:
        return "SUPPORTIVE"
    return "DECORATIVE"


def format_results_table(claim_results: list[dict]) -> str:
    """Format claim results as a markdown table.

    Each entry should have: claim_id, claim_text, avg_score, classification.
    """
    if not claim_results:
        return "No claims were tested."

    lines = [
        "| # | Claim | Avg Impact | Classification |",
        "|---|-------|-----------|----------------|",
    ]
    for r in claim_results:
        cid = r.get("claim_id", "?")
        text = r.get("claim_text", "")
        avg = r.get("avg_score")
        avg_str = f"{avg:.1f}" if avg is not None else "N/A"
        cls = r.get("classification") or "N/A"
        lines.append(f"| {cid} | {text} | {avg_str} | {cls} |")
    return "\n".join(lines)
