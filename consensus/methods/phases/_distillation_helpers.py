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

#: Lower/upper bound for a validity or overall score (1-5 Likert scale).
VALIDITY_SCORE_MIN = 1
VALIDITY_SCORE_MAX = 5

#: JSON Schema for the submit_validity_scores output tool (issue #23).
#: One entry per inference/conclusion step (keyed by skeleton id, e.g.
#: "I1", "C1") plus a single overall score for the whole argument.
VALIDITY_TOOL_PARAMETERS: dict = {
    "type": "object",
    "properties": {
        "scores": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "inference_id": {
                        "type": "string",
                        "description": (
                            "The id of the inference or conclusion step "
                            "being scored (e.g. 'I1', 'C1')."
                        ),
                    },
                    "score": {
                        "type": "integer",
                        "minimum": VALIDITY_SCORE_MIN,
                        "maximum": VALIDITY_SCORE_MAX,
                        "description": (
                            "Logical validity score for this step "
                            "(1=fallacious, 5=airtight)."
                        ),
                    },
                },
                "required": ["inference_id", "score"],
            },
            "description": (
                "One entry per inference/conclusion step being evaluated."
            ),
        },
        "overall": {
            "type": "integer",
            "minimum": VALIDITY_SCORE_MIN,
            "maximum": VALIDITY_SCORE_MAX,
            "description": (
                "Overall validity score for the whole argument (1-5)."
            ),
        },
        "reasoning": {
            "type": "string",
            "description": (
                "Your independent assessment rationale across the "
                "scored inferences: which steps follow from their "
                "stated dependencies, which have gaps or unstated "
                "assumptions, and why."
            ),
        },
    },
    "required": ["scores", "overall", "reasoning"],
}


def validate_validity_scores_payload(payload: dict,
                                     eval_item_ids: list[str]) -> str:
    """Return '' if a submit_validity_scores payload is usable, else an error.

    Every id in ``eval_item_ids`` (the skeleton's inference and
    conclusion steps) must appear exactly once in ``scores`` with a
    1-5 integer score, ``overall`` must also be a 1-5 integer, and
    ``reasoning`` must be non-empty prose.  Unknown ids and missing
    ids are named explicitly so the model can correct its next attempt.
    """
    scores = payload.get("scores")
    if not isinstance(scores, list) or not scores:
        return ("'scores' must be a non-empty array, one entry per "
                "inference/conclusion step.")

    valid_ids = {item_id.upper() for item_id in eval_item_ids}
    seen: set[str] = set()
    for entry in scores:
        if not isinstance(entry, dict):
            return "Each entry in 'scores' must be an object."
        raw_id = entry.get("inference_id")
        if not isinstance(raw_id, str) or not raw_id:
            return "Each score entry needs a string 'inference_id'."
        item_id = raw_id.upper()
        if item_id not in valid_ids:
            return (f"Unknown inference/conclusion id '{raw_id}'. "
                    f"Valid ids: {sorted(valid_ids)}.")
        if item_id in seen:
            return (f"'{item_id}' appears more than once — submit "
                    "exactly one score per step.")
        seen.add(item_id)

        score = entry.get("score")
        try:
            score_val = int(score)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return (f"The score for '{item_id}' must be an integer "
                    f"between {VALIDITY_SCORE_MIN} and "
                    f"{VALIDITY_SCORE_MAX}.")
        if not (VALIDITY_SCORE_MIN <= score_val <= VALIDITY_SCORE_MAX):
            return (f"The score for '{item_id}' must be between "
                    f"{VALIDITY_SCORE_MIN} and {VALIDITY_SCORE_MAX}.")

    missing = sorted(valid_ids - seen)
    if missing:
        return f"Provide a score for every step. Missing: {missing}."

    overall = payload.get("overall")
    if overall is None:
        return ("'overall' is required: an integer "
                f"{VALIDITY_SCORE_MIN}-{VALIDITY_SCORE_MAX} score for "
                "the whole argument.")
    try:
        overall_val = int(overall)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return (f"'overall' must be an integer between "
                f"{VALIDITY_SCORE_MIN} and {VALIDITY_SCORE_MAX}.")
    if not (VALIDITY_SCORE_MIN <= overall_val <= VALIDITY_SCORE_MAX):
        return (f"'overall' must be between {VALIDITY_SCORE_MIN} and "
                f"{VALIDITY_SCORE_MAX}.")

    if not str(payload.get("reasoning", "")).strip():
        return "'reasoning' must contain your assessment rationale."

    return ""


def format_validity_scores_display(scores: dict[str, int],
                                   overall: int,
                                   reasoning: str = "") -> str:
    """Render a validated submit_validity_scores payload as display text.

    The evaluator's ``reasoning`` prose (if any) comes first so the
    transcript keeps the deliberation, followed by the tag lines.
    Keeps the ``[VALIDITY id: n]``/``[OVERALL: n]`` tags in the output
    so ``BlindEvaluateHandler.filter_context_message`` still recognises
    this as an in-phase evaluation message (its blindness filter
    matches on the literal ``[VALIDITY`` substring) — the free-text and
    structured paths must both keep those tags visible.
    """
    tags = [f"[VALIDITY {item_id}: {score}]"
            for item_id, score in sorted(scores.items())]
    tags.append(f"[OVERALL: {overall}]")

    bar_parts = [f"{item_id}: {score}/5"
                 for item_id, score in sorted(scores.items())]
    bar_parts.append(f"Overall: {overall}/5")

    body = ("\n".join(tags) + "\n\n---\n**Validity scores:** "
            + " | ".join(bar_parts))
    reasoning = reasoning.strip()
    return f"{reasoning}\n\n{body}" if reasoning else body


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
