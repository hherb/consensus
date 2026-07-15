"""Recording and validation helpers for MCDA phase handlers (issue #25).

Constants, JSON Schemas, payload validators, option/criterion/score
recording and deduplication, and free-text extraction — used by the
options, criteria, score, and decide phase handlers.  The numeric
analysis and formatting layer lives in ``_mcda_analysis.py``.
"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING

from ..parsing import (
    canonical_index,
    cluster_by_similarity,
    cluster_text_contributions,
    extract_json_block,
    parse_numbered_list,
    word_overlap_similar,
)

if TYPE_CHECKING:
    from ...models import Entity

logger = logging.getLogger(__name__)

#: Inclusive bounds for a criterion importance weight.
WEIGHT_MIN = 1
WEIGHT_MAX = 5
#: Weight assumed when a free-text criterion carries no explicit weight.
DEFAULT_WEIGHT = 3
#: Inclusive bounds for an option-against-criterion score.
SCORE_MIN = 1
SCORE_MAX = 5
#: Score assumed for a cell no participant scored (scale midpoint).
DEFAULT_SCORE = 3
#: Minimum character length for an option to be substantive.  Short on
#: purpose: legitimate options can be terse ("Rent", "Kotlin").
MIN_OPTION_LENGTH = 3
#: Minimum character length for a criterion name ("Cost" must pass).
MIN_CRITERION_LENGTH = 3
#: Word-overlap ratio above which two options/criteria are duplicates.
SIMILARITY_THRESHOLD = 0.7
#: Give up and advance after this many option-enumeration rounds.
MAX_OPTIONS_ROUNDS = 3
#: Give up and advance after this many criteria rounds.
MAX_CRITERIA_ROUNDS = 4
#: Fraction of the top weighted total within which second place counts
#: as a close call in the sensitivity report.
CLOSE_CALL_MARGIN = 0.05

#: JSON Schema for the submit_options output tool (issue #23 pattern).
OPTIONS_TOOL_PARAMETERS: dict = {
    "type": "object",
    "properties": {
        "options": {
            "type": "array",
            "minItems": 1,
            "items": {"type": "string"},
            "description": ("The decision alternatives — each one a "
                            "distinct, self-contained option the group "
                            "could choose.  Include options already "
                            "named in the topic plus any missing "
                            "alternatives worth considering."),
        },
        "reasoning": {
            "type": "string",
            "description": ("Brief rationale: why these are the "
                            "relevant alternatives."),
        },
    },
    "required": ["options", "reasoning"],
}

#: JSON Schema for the submit_weighted_criteria output tool.
CRITERIA_TOOL_PARAMETERS: dict = {
    "type": "object",
    "properties": {
        "criteria": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": ("The decision criterion — short, "
                                        "specific, and measurable."),
                    },
                    "weight": {
                        "type": "integer",
                        "minimum": WEIGHT_MIN,
                        "maximum": WEIGHT_MAX,
                        "description": (f"Importance weight, {WEIGHT_MIN} "
                                        f"(minor) to {WEIGHT_MAX} "
                                        "(decisive)."),
                    },
                    "rationale": {
                        "type": "string",
                        "description": ("Optional: why this criterion "
                                        "deserves this weight."),
                    },
                },
                "required": ["name", "weight"],
            },
        },
        "reasoning": {
            "type": "string",
            "description": ("Your overall rationale for this criteria "
                            "set and its weights."),
        },
    },
    "required": ["criteria", "reasoning"],
}

#: JSON Schema for the submit_scores output tool.  ``scores`` nests two
#: levels of dynamic keys — option label (O1, O2, ...) then criterion
#: label (C1, C2, ...) — so both levels use ``additionalProperties``
#: (the ``MATRIX_TOOL_PARAMETERS`` pattern in ``evaluate_matrix.py``).
SCORES_TOOL_PARAMETERS: dict = {
    "type": "object",
    "properties": {
        "scores": {
            "type": "object",
            "description": ("Map of every option label (O1, O2, ...) to "
                            "an object mapping every criterion label "
                            "(C1, C2, ...) to your integer score "
                            f"({SCORE_MIN}-{SCORE_MAX})."),
            "additionalProperties": {
                "type": "object",
                "additionalProperties": {
                    "type": "integer",
                    "minimum": SCORE_MIN,
                    "maximum": SCORE_MAX,
                },
            },
        },
        "reasoning": {
            "type": "string",
            "description": ("Your rationale for these scores — explain "
                            "the extremes: why does an option score "
                            "highest or lowest on a criterion?"),
        },
    },
    "required": ["scores", "reasoning"],
}

#: JSON Schema for the submit_decision output tool.  The required
#: ``rationale`` plays the usual ``reasoning`` role (like
#: ``submit_claims``' ``preliminary_conclusion``).
DECISION_TOOL_PARAMETERS: dict = {
    "type": "object",
    "properties": {
        "recommended_option_id": {
            "type": "integer",
            "description": ("The numeric id of the recommended option "
                            "(from the O-labels)."),
        },
        "rationale": {
            "type": "string",
            "description": ("Why this option wins: which weighted "
                            "criteria drove the result, and how the "
                            "sensitivity analysis bears on it."),
        },
        "caveats": {
            "type": "array",
            "items": {"type": "string"},
            "description": ("Optional caveats: close calls, pivotal "
                            "criteria, strong divergence, or conditions "
                            "under which the decision should be "
                            "revisited."),
        },
    },
    "required": ["recommended_option_id", "rationale"],
}


def option_label(option_id: int) -> str:
    """Return the display label for an option id (``O3``)."""
    return f"O{option_id}"


def criterion_label(criterion_id: int) -> str:
    """Return the display label for a criterion id (``C2``)."""
    return f"C{criterion_id}"


def validate_options_payload(payload: dict) -> str:
    """Return '' if a submit_options payload is usable, else an error."""
    options = payload.get("options")
    if not isinstance(options, list) or not options:
        return "'options' must be a non-empty array of option strings."
    for opt in options:
        if not isinstance(opt, str) or len(opt.strip()) < MIN_OPTION_LENGTH:
            return ("Each option must be a distinct alternative of at "
                    f"least {MIN_OPTION_LENGTH} characters (got: {opt!r}).")
    if not str(payload.get("reasoning") or "").strip():
        return "'reasoning' must contain your rationale for these options."
    return ""


def record_options(state: dict, entity: Entity,
                   texts: list[str]) -> list[dict]:
    """Append this turn's options as raw contributions, rebuild the
    order-independent clustered view, and return the clusters this
    turn's options landed in.

    Every submission is retained in ``state["options_raw"]``; the merged
    view ``state["options"]`` is derived by clustering the whole raw set
    and labelling each cluster with its medoid, so grouping and label are
    independent of submission order (issue #42).  Ids are referenced
    downstream as ``O1..On`` and are frozen once the scoring phase
    begins (no options are added there).  Shared by the free-text and
    structured-output paths (issue #23).
    """
    raw = state.setdefault("options_raw", [])
    since = len(raw)
    for text in texts:
        cleaned = str(text).strip().rstrip('.')
        if len(cleaned) < MIN_OPTION_LENGTH:
            continue
        raw.append({"entity_id": entity.id, "entity_name": entity.name,
                    "text": cleaned})
    view, touched = cluster_text_contributions(
        raw, since=since, threshold=SIMILARITY_THRESHOLD)
    state["options"] = view
    return touched


def validate_criteria_payload(payload: dict) -> str:
    """Return '' if a submit_weighted_criteria payload is usable, else error."""
    criteria = payload.get("criteria")
    if not isinstance(criteria, list) or not criteria:
        return ("'criteria' must be a non-empty array of "
                "{name, weight} objects.")
    for c in criteria:
        if not isinstance(c, dict):
            return "Each entry in 'criteria' must be an object."
        name = c.get("name")
        if (not isinstance(name, str)
                or len(name.strip()) < MIN_CRITERION_LENGTH):
            return ("Each criterion 'name' must be a specific, measurable "
                    f"criterion of at least {MIN_CRITERION_LENGTH} "
                    f"characters (got: {name!r}).")
        weight = c.get("weight")
        if (isinstance(weight, bool) or not isinstance(weight, int)
                or not WEIGHT_MIN <= weight <= WEIGHT_MAX):
            return (f"Each criterion 'weight' must be an integer between "
                    f"{WEIGHT_MIN} and {WEIGHT_MAX} (got: {weight!r}).")
        rationale = c.get("rationale")
        if rationale is not None and not isinstance(rationale, str):
            return "Each criterion 'rationale' must be a string when present."
    if not str(payload.get("reasoning") or "").strip():
        return "'reasoning' must explain this criteria set and its weights."
    return ""


def record_criteria(state: dict, entity: Entity,
                    items: list[dict]) -> list[dict]:
    """Merge criteria by name similarity and record weight votes.

    A criterion similar to an existing one adds (or replaces — the
    refinement round) this entity's weight vote on it; otherwise a new
    criterion is appended.  Weights are clamped into
    ``[WEIGHT_MIN, WEIGHT_MAX]`` (the free-text path may carry
    arbitrary numbers).  Returns the criterion dicts touched.
    """
    criteria = state.setdefault("criteria", [])
    touched: list[dict] = []
    for item in items:
        name = str(item.get("name") or "").strip().rstrip('.')
        if len(name) < MIN_CRITERION_LENGTH:
            continue
        try:
            weight = int(item.get("weight", DEFAULT_WEIGHT))
        except (TypeError, ValueError):
            weight = DEFAULT_WEIGHT
        weight = min(max(weight, WEIGHT_MIN), WEIGHT_MAX)
        existing = next(
            (c for c in criteria
             if word_overlap_similar(name, c["name"],
                                     threshold=SIMILARITY_THRESHOLD)),
            None,
        )
        if existing is None:
            existing = {"id": len(criteria) + 1, "name": name,
                        "weight_votes": {}}
            criteria.append(existing)
        existing["weight_votes"][str(entity.id)] = weight
        if not any(t is existing for t in touched):
            touched.append(existing)
    return touched


def criterion_weight(criterion: dict) -> float:
    """Effective weight of a criterion: the mean of its weight votes."""
    votes = criterion.get("weight_votes", {})
    if not votes:
        return float(DEFAULT_WEIGHT)
    return sum(votes.values()) / len(votes)


#: Trailing "(weight: N)" / "[weight = N]" marker on a free-text
#: criterion line.
_WEIGHT_SUFFIX = re.compile(
    r'[\(\[]\s*weight\s*[:=]?\s*(\d+)\s*[\)\]]\s*$', re.IGNORECASE)


def extract_weighted_criteria(content: str) -> list[dict]:
    """Parse ``1. Cost (weight: 4)`` style lines from free text.

    Items without an explicit weight marker get ``DEFAULT_WEIGHT``.
    Returns ``{"name": str, "weight": int}`` dicts (weights clamped by
    ``record_criteria``, not here).
    """
    items = parse_numbered_list(content, min_length=MIN_CRITERION_LENGTH)
    parsed: list[dict] = []
    for item in items:
        match = _WEIGHT_SUFFIX.search(item)
        if match:
            name = item[:match.start()].strip().rstrip('.')
            weight = int(match.group(1))
        else:
            name, weight = item, DEFAULT_WEIGHT
        if len(name) >= MIN_CRITERION_LENGTH:
            parsed.append({"name": name, "weight": weight})
    return parsed


def validate_scores_payload(payload: dict, options: list[dict],
                            criteria: list[dict]) -> str:
    """Return '' if a submit_scores payload is usable, else an error.

    Option keys must be a subset of the recorded O-labels and criterion
    keys a subset of the C-labels (unknown labels are named in the
    error, along with the valid set).  Every score must be an integer
    in ``[SCORE_MIN, SCORE_MAX]``.  Coverage may be partial: the
    aggregation defaults unscored cells to ``DEFAULT_SCORE`` (the ACH
    matrix precedent), so this validator holds the same bar.
    """
    scores = payload.get("scores")
    if not isinstance(scores, dict) or not scores:
        return ("'scores' must be a non-empty object mapping each option "
                "label (O1, O2, ...) to a scores object.")
    valid_o = [option_label(o["id"]) for o in options]
    unknown_o = [key for key in scores if key not in valid_o]
    if unknown_o:
        return (f"Unknown option label(s) {unknown_o}. "
                f"Valid labels: {valid_o}.")
    valid_c = [criterion_label(c["id"]) for c in criteria]
    for okey, row in scores.items():
        if not isinstance(row, dict) or not row:
            return (f"The scores for '{okey}' must be a non-empty object "
                    "mapping each criterion label to a score.")
        unknown_c = [key for key in row if key not in valid_c]
        if unknown_c:
            return (f"Unknown criterion label(s) {unknown_c} for '{okey}'. "
                    f"Valid labels: {valid_c}.")
        for ckey, score in row.items():
            if (isinstance(score, bool) or not isinstance(score, int)
                    or not SCORE_MIN <= score <= SCORE_MAX):
                return (f"The score for '{okey}'/'{ckey}' must be an "
                        f"integer between {SCORE_MIN} and {SCORE_MAX} "
                        f"(got: {score!r}).")
    if not str(payload.get("reasoning") or "").strip():
        return "'reasoning' must contain your rationale for these scores."
    return ""


def record_scores(state: dict, entity: Entity, scores: dict) -> int:
    """Sanitise and store one participant's score matrix.

    Keeps only cells addressing a recorded option and criterion label
    with an integer-coercible, in-range score (the free-text path may
    carry junk labels, quoted numbers, or junk values; booleans are
    rejected — bool is an int subtype and ``True`` would silently
    count as a score of 1).  Unknown labels are dropped rather than
    stored: a stored-but-unaggregatable matrix would count its author
    as a scorer with every cell defaulted, inflating divergence.
    Returns the number of cells kept; records nothing when no cell
    survives.  Shared by the free-text and structured paths.
    """
    valid_o = {option_label(o["id"]) for o in state.get("options", [])}
    valid_c = {criterion_label(c["id"]) for c in state.get("criteria", [])}
    cleaned: dict[str, dict[str, int]] = {}
    kept = 0
    for okey, row in scores.items():
        if str(okey) not in valid_o or not isinstance(row, dict):
            continue
        for ckey, value in row.items():
            if str(ckey) not in valid_c or isinstance(value, bool):
                continue
            try:
                score = int(value)
            except (TypeError, ValueError):
                continue
            if not SCORE_MIN <= score <= SCORE_MAX:
                continue
            cleaned.setdefault(str(okey), {})[str(ckey)] = score
            kept += 1
    if cleaned:
        state.setdefault("scores", {})[str(entity.id)] = cleaned
    return kept


def extract_scores(content: str) -> dict:
    """Parse a scores mapping from free text (human/fallback path).

    Tries a fenced JSON block with a ``scores`` object first, then an
    inline (unfenced) JSON object — scanned to its balanced closing
    brace, since a lazy regex would truncate at the first inner brace
    (the ``evaluate_matrix._parse_ratings`` pattern).
    """
    data = extract_json_block(content)
    if isinstance(data, dict) and isinstance(data.get("scores"), dict):
        return data["scores"]
    start = content.find('{"scores"')
    if start != -1:
        depth = 0
        for pos in range(start, len(content)):
            if content[pos] == "{":
                depth += 1
            elif content[pos] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        data = json.loads(content[start:pos + 1])
                        scores = data.get("scores", {})
                        return scores if isinstance(scores, dict) else {}
                    except (json.JSONDecodeError, ValueError):
                        pass
                    break
    return {}


def validate_decision_payload(payload: dict, valid_ids: set[int]) -> str:
    """Return '' if a submit_decision payload is usable, else an error."""
    raw_id = payload.get("recommended_option_id")
    if isinstance(raw_id, bool) or not isinstance(raw_id, int):
        return "'recommended_option_id' must be an integer option id."
    if raw_id not in valid_ids:
        return (f"Option {raw_id} does not exist. Valid option ids: "
                f"{sorted(valid_ids)}.")
    if not str(payload.get("rationale") or "").strip():
        return "'rationale' must explain why this option wins."
    caveats = payload.get("caveats")
    if caveats is not None:
        if (not isinstance(caveats, list)
                or any(not isinstance(c, str) for c in caveats)):
            return "'caveats' must be an array of strings when present."
    return ""
