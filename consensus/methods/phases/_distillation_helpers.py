"""Helper functions for Recursive Self-Distillation.

Pure functions for skeleton validation, validity score extraction,
classification, and table formatting.
"""

from __future__ import annotations

import re


def validate_skeleton(data: dict) -> bool:
    """Validate that parsed JSON has the expected skeleton structure.

    Expected schema::

        {
            "premises": [{"id": "P1", "text": "..."}],
            "inferences": [{"id": "I1", "from": ["P1"], "text": "..."}],
            "conclusions": [{"id": "C1", "from": ["I1"], "text": "..."}]
        }
    """
    if not isinstance(data, dict):
        return False

    for key in ("premises", "inferences", "conclusions"):
        items = data.get(key)
        if not isinstance(items, list) or not items:
            return False
        for item in items:
            if not isinstance(item, dict):
                return False
            if not item.get("id") or not item.get("text"):
                return False

    # Collect all valid IDs for reference checking
    valid_ids: set[str] = set()
    for p in data["premises"]:
        valid_ids.add(p["id"])

    # Inferences and conclusions must have "from" lists pointing to real IDs
    for key in ("inferences", "conclusions"):
        for item in data[key]:
            refs = item.get("from")
            if not isinstance(refs, list) or not refs:
                return False
            if not all(ref in valid_ids for ref in refs):
                return False
            # Inferences become valid targets for later references
            if key == "inferences":
                valid_ids.add(item["id"])

    return True


def format_skeleton_display(skeleton: dict) -> str:
    """Format a skeleton as readable markdown."""
    lines: list[str] = []

    lines.append("**Premises:**")
    for p in skeleton["premises"]:
        lines.append(f"- {p['id']}: {p['text']}")

    lines.append("")
    lines.append("**Inferences:**")
    for inf in skeleton["inferences"]:
        deps = ", ".join(inf["from"])
        lines.append(f"- {inf['id']} (from {deps}): {inf['text']}")

    lines.append("")
    lines.append("**Conclusions:**")
    for c in skeleton["conclusions"]:
        deps = ", ".join(c["from"])
        lines.append(f"- {c['id']} (from {deps}): {c['text']}")

    return "\n".join(lines)


_VALIDITY_RE = re.compile(
    r"\[VALIDITY\s+(\w+):\s*(\d)\s*\]", re.IGNORECASE,
)
_OVERALL_RE = re.compile(
    r"\[OVERALL:\s*(\d)\s*\]", re.IGNORECASE,
)


def extract_validity_scores(content: str) -> dict[str, int]:
    """Parse ``[VALIDITY Ix: N]`` tags from response content.

    Returns a mapping of inference/conclusion ID to score (1-5).
    """
    scores: dict[str, int] = {}
    for match in _VALIDITY_RE.finditer(content):
        item_id = match.group(1).upper()
        score = int(match.group(2))
        score = max(1, min(5, score))
        scores[item_id] = score
    return scores


def extract_overall_score(content: str) -> int | None:
    """Parse ``[OVERALL: N]`` tag from response content."""
    match = _OVERALL_RE.search(content)
    if match:
        score = int(match.group(1))
        return max(1, min(5, score))
    return None


def compute_average_validity(
    validity_scores: dict[str, dict[str, int]],
) -> dict[str, float]:
    """Compute average validity score per inference across evaluators.

    Args:
        validity_scores: ``{inference_id: {entity_name: score}}``

    Returns:
        ``{inference_id: average_score}``
    """
    averages: dict[str, float] = {}
    for item_id, scores in validity_scores.items():
        if scores:
            averages[item_id] = sum(scores.values()) / len(scores)
    return averages


def classify_inference(avg_score: float) -> str:
    """Classify an inference step by average validity score."""
    if avg_score >= 4.0:
        return "SOUND"
    if avg_score >= 2.5:
        return "QUESTIONABLE"
    return "WEAK"


def format_validity_table(
    skeleton: dict,
    validity_scores: dict[str, dict[str, int]],
) -> str:
    """Build a markdown table of validity results for the conclusion prompt."""
    averages = compute_average_validity(validity_scores)

    # Collect all inference and conclusion items
    items = list(skeleton.get("inferences", []))
    items.extend(skeleton.get("conclusions", []))

    lines: list[str] = [
        "| Step | Depends On | Statement | Avg Validity | Classification |",
        "|------|-----------|-----------|:------------:|----------------|",
    ]

    for item in items:
        item_id = item["id"]
        deps = ", ".join(item.get("from", []))
        text = item["text"][:80] + ("..." if len(item["text"]) > 80 else "")
        avg = averages.get(item_id)
        if avg is not None:
            classification = classify_inference(avg)
            lines.append(
                f"| {item_id} | {deps} | {text} | {avg:.1f} | {classification} |"
            )
        else:
            lines.append(f"| {item_id} | {deps} | {text} | — | — |")

    return "\n".join(lines)
