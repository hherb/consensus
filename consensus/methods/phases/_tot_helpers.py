"""Recording and validation helpers for Tree of Thoughts (issue #26).

Constants, JSON Schemas, payload validators, thought/score/expansion
recording with deduplication, and eligibility/depth queries — used
by the propose, score, prune, expand, and synthesise phase handlers.
The free-text JSON extraction lives in ``..parsing.extract_json_payload``.  The deterministic
composite/beam/artifact/formatting layer lives in ``_tot_analysis.py``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ..parsing import cluster_text_contributions, validate_string_list_payload

if TYPE_CHECKING:
    from ...models import Entity

logger = logging.getLogger(__name__)

#: Inclusive bounds for a dimension score.
SCORE_MIN = 1
SCORE_MAX = 5
#: Score assumed per dimension for a thought nobody scored (midpoint).
DEFAULT_DIMENSION_SCORE = 3
#: The fixed scoring dimensions (issue #26: feasibility/impact/risk).
DIMENSIONS: tuple[str, ...] = ("feasibility", "impact", "risk")
#: Survivors kept by each prune pass (issue #26: "top 2-3").
BEAM_WIDTH = 3
#: A beam smaller than this has nothing to explore in parallel — the
#: prune phase routes straight to synthesis (degenerate tree).
MIN_BEAM_SIZE = 2
#: Maximum score→prune→expand passes before forced synthesis.
MAX_TOT_DEPTH = 3
#: Give up and advance after this many propose rounds without thoughts.
MAX_PROPOSE_ROUNDS = 3
#: Minimum character length for a thought to be substantive.
MIN_THOUGHT_LENGTH = 10
#: Minimum character length for an expansion refinement.
MIN_REFINEMENT_LENGTH = 10
#: Word-overlap ratio above which two thoughts are duplicates.
SIMILARITY_THRESHOLD = 0.7

#: Stop reasons recorded in the outcome artifact.
STOP_CONVERGED = "converged"
STOP_DEPTH = "depth_budget"
STOP_DEGENERATE = "degenerate"

#: JSON Schema for the submit_thoughts output tool (issue #23 pattern).
THOUGHTS_TOOL_PARAMETERS: dict = {
    "type": "object",
    "properties": {
        "thoughts": {
            "type": "array",
            "minItems": 1,
            "items": {"type": "string"},
            "description": ("Your candidate solution approaches — each a "
                            "complete, specific, self-contained strategy.  "
                            "Aim for 2-5 genuinely distinct approaches "
                            "(different strategies, not variations of "
                            "one)."),
        },
        "reasoning": {
            "type": "string",
            "description": ("Brief rationale: the angle each approach "
                            "explores."),
        },
    },
    "required": ["thoughts", "reasoning"],
}

#: JSON Schema for the submit_thought_scores output tool.  Thought
#: labels (T1, T2, ...) are only known at runtime, so the outer level
#: uses ``additionalProperties``; the dimensions are fixed, so the
#: inner object enumerates and requires all three.
SCORES_TOOL_PARAMETERS: dict = {
    "type": "object",
    "properties": {
        "scores": {
            "type": "object",
            "description": ("Map of each thought label (T1, T2, ...) to "
                            "your three dimension scores."),
            "additionalProperties": {
                "type": "object",
                "properties": {
                    dim: {
                        "type": "integer",
                        "minimum": SCORE_MIN,
                        "maximum": SCORE_MAX,
                        "description": desc,
                    }
                    for dim, desc in (
                        ("feasibility", "How realistically this approach "
                                        "can be executed "
                                        f"({SCORE_MIN}=infeasible, "
                                        f"{SCORE_MAX}=straightforward)."),
                        ("impact", "How much this approach would achieve "
                                   f"if it works ({SCORE_MIN}=marginal, "
                                   f"{SCORE_MAX}=transformative)."),
                        ("risk", "How likely this approach is to fail or "
                                 f"backfire ({SCORE_MIN}=safe, "
                                 f"{SCORE_MAX}=very risky)."),
                    )
                },
                "required": list(DIMENSIONS),
            },
        },
        "reasoning": {
            "type": "string",
            "description": ("Your rationale — explain the extremes: what "
                            "makes an approach most feasible, most "
                            "impactful, or most risky?"),
        },
    },
    "required": ["scores", "reasoning"],
}

#: JSON Schema for the submit_expansions output tool (deep-dive round).
EXPANSIONS_TOOL_PARAMETERS: dict = {
    "type": "object",
    "properties": {
        "expansions": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "properties": {
                    "thought_id": {
                        "type": "integer",
                        "description": ("Numeric id of the surviving "
                                        "thought (from the T-labels)."),
                    },
                    "refinement": {
                        "type": "string",
                        "description": ("How to strengthen and concretise "
                                        "this approach — next steps, "
                                        "scope, mechanism."),
                    },
                    "obstacles": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": ("Concrete obstacles that could "
                                        "make this approach fail."),
                    },
                },
                "required": ["thought_id", "refinement"],
            },
            "description": ("One entry per surviving thought you "
                            "deep-dive (cover as many as you can)."),
        },
        "reasoning": {
            "type": "string",
            "description": ("Your overall read on where the surviving "
                            "approaches stand after the deep-dive."),
        },
    },
    "required": ["expansions", "reasoning"],
}


def thought_label(thought_id: int) -> str:
    """Return the display label for a thought id (``T3``)."""
    return f"T{thought_id}"


# ----------------------------------------------------------------------
# Propose phase
# ----------------------------------------------------------------------

def validate_thoughts_payload(payload: dict) -> str:
    """Return '' if a submit_thoughts payload is usable, else an error."""
    return validate_string_list_payload(
        payload, key="thoughts", min_length=MIN_THOUGHT_LENGTH,
        empty_error=("'thoughts' must be a non-empty array of approach "
                     "strings."),
        item_error=("Each thought must be a complete, specific approach of "
                    "at least {min_length} characters (got: {item!r})."),
        reasoning_error=("'reasoning' must contain your rationale for "
                         "these approaches."))


def record_thoughts(state: dict, entity: Entity,
                    texts: list[str]) -> list[dict]:
    """Append this turn's thoughts as raw contributions, rebuild the
    order-independent clustered view, and return the clusters this
    turn's thoughts landed in.

    Every submission is retained in ``state["thoughts_raw"]``; the merged
    view ``state["thoughts"]`` is derived by clustering the whole raw set
    and labelling each cluster with its medoid, so grouping and label are
    independent of submission order (issue #42).  Scoring + pruning
    filters whatever survives this coarse gate.  Shared by the free-text
    and structured-output paths (issue #23).
    """
    raw = state.setdefault("thoughts_raw", [])
    since = len(raw)
    for text in texts:
        cleaned = str(text).strip()
        if cleaned.endswith(".") and not cleaned.endswith(".."):
            cleaned = cleaned[:-1]  # lone full stop, not an ellipsis
        if len(cleaned) < MIN_THOUGHT_LENGTH:
            continue
        raw.append({"entity_id": entity.id, "entity_name": entity.name,
                    "text": cleaned})
    view, touched = cluster_text_contributions(
        raw, since=since, threshold=SIMILARITY_THRESHOLD)
    state["thoughts"] = view
    return touched


# ----------------------------------------------------------------------
# Eligibility & depth
# ----------------------------------------------------------------------

def current_depth(state: dict) -> int:
    """Number of completed prune passes (``beam_history`` length)."""
    return len(state.get("beam_history", []))


def eligible_thoughts(state: dict) -> list[dict]:
    """Thoughts still in play: all before the first prune, else the beam.

    Pruned branches are dead — scoring and expansion only address the
    latest beam once one exists.
    """
    thoughts = state.get("thoughts", [])
    history = state.get("beam_history", [])
    if not history:
        return list(thoughts)
    beam_ids = set(history[-1].get("beam_ids", []))
    return [t for t in thoughts if t["id"] in beam_ids]


# ----------------------------------------------------------------------
# Score phase
# ----------------------------------------------------------------------

def validate_scores_payload(payload: dict,
                            eligible: list[dict]) -> str:
    """Return '' if a submit_thought_scores payload is usable, else error.

    Labels must be a subset of the eligible thought labels (pruned or
    unknown labels are named in the error along with the valid set);
    every entry must carry all three dimensions as in-range integers
    (booleans rejected — bool is an int subtype).
    """
    scores = payload.get("scores")
    if not isinstance(scores, dict) or not scores:
        return ("'scores' must be a non-empty object mapping each thought "
                "label (T1, T2, ...) to your dimension scores.")
    valid = [thought_label(t["id"]) for t in eligible]
    unknown = [key for key in scores if key not in valid]
    if unknown:
        return (f"Unknown or pruned thought label(s) {unknown}. "
                f"Valid labels: {valid}.")
    for key, entry in scores.items():
        if not isinstance(entry, dict):
            return (f"The scores for '{key}' must be an object with "
                    f"{', '.join(DIMENSIONS)}.")
        for dim in DIMENSIONS:
            value = entry.get(dim)
            if (isinstance(value, bool) or not isinstance(value, int)
                    or not SCORE_MIN <= value <= SCORE_MAX):
                return (f"'{key}' needs an integer '{dim}' between "
                        f"{SCORE_MIN} and {SCORE_MAX} (got: {value!r}).")
    if not str(payload.get("reasoning") or "").strip():
        return "'reasoning' must contain your rationale for these scores."
    return ""


def record_thought_scores(state: dict, entity: Entity,
                          scores: dict) -> int:
    """Sanitise and merge one participant's dimension scores.

    Keeps only complete, in-range entries addressing an eligible
    thought label (the free-text path may carry junk labels, quoted
    numbers, or junk values; booleans are rejected).  Entries merge
    **per thought label** into the entity's existing scores, so a
    re-score pass replaces only the thoughts it addresses — earlier
    scores for pruned thoughts stay recorded but are ignored by the
    eligibility-scoped aggregation.  ``scores_by_pass`` records which
    labels received a fresh score in each pass (the prune phase's
    convergence gate demands full beam coverage).  Returns the number
    of thought entries kept.  Shared by the free-text and structured
    paths.
    """
    valid = {thought_label(t["id"]) for t in eligible_thoughts(state)}
    kept = 0
    cleaned: dict[str, dict[str, int]] = {}
    for key, entry in scores.items():
        if str(key) not in valid or not isinstance(entry, dict):
            continue
        values: dict[str, int] = {}
        for dim in DIMENSIONS:
            raw = entry.get(dim)
            if isinstance(raw, bool):
                break
            try:
                value = int(raw)
            except (TypeError, ValueError):
                break
            if not SCORE_MIN <= value <= SCORE_MAX:
                break
            values[dim] = value
        if len(values) == len(DIMENSIONS):
            cleaned[str(key)] = values
            kept += 1
    if cleaned:
        entity_scores = state.setdefault("thought_scores", {}).setdefault(
            str(entity.id), {})
        entity_scores.update(cleaned)
        by_pass = state.setdefault("scores_by_pass", {})
        fresh = by_pass.setdefault(str(current_depth(state)), [])
        fresh.extend(label for label in cleaned if label not in fresh)
    return kept


# ----------------------------------------------------------------------
# Expand phase
# ----------------------------------------------------------------------

def validate_expansions_payload(payload: dict,
                                beam_ids: set[int]) -> str:
    """Return '' if a submit_expansions payload is usable, else an error."""
    expansions = payload.get("expansions")
    if not isinstance(expansions, list) or not expansions:
        return ("'expansions' must be a non-empty array of "
                "{thought_id, refinement} objects.")
    seen: set[int] = set()
    for entry in expansions:
        if not isinstance(entry, dict):
            return "Each entry in 'expansions' must be an object."
        raw_id = entry.get("thought_id")
        if isinstance(raw_id, bool) or not isinstance(raw_id, int):
            return "'thought_id' must be an integer thought id."
        if raw_id not in beam_ids:
            return (f"Thought {raw_id} is not in the surviving beam. "
                    f"Valid thought ids: {sorted(beam_ids)}.")
        if raw_id in seen:
            return (f"Duplicate entry for thought {raw_id} — merge your "
                    "points into one entry per surviving thought.")
        seen.add(raw_id)
        refinement = entry.get("refinement")
        if (not isinstance(refinement, str)
                or len(refinement.strip()) < MIN_REFINEMENT_LENGTH):
            return ("Each 'refinement' must concretise the approach in at "
                    f"least {MIN_REFINEMENT_LENGTH} characters "
                    f"(got: {refinement!r}).")
        obstacles = entry.get("obstacles")
        if obstacles is not None:
            if (not isinstance(obstacles, list)
                    or any(not isinstance(o, str) for o in obstacles)):
                return "'obstacles' must be an array of strings when present."
    if not str(payload.get("reasoning") or "").strip():
        return "'reasoning' must contain your overall read on the survivors."
    return ""


def record_expansions(state: dict, entity: Entity,
                      items: list[dict], depth: int) -> int:
    """Sanitise and append depth-tagged expansions; return count accepted.

    Skips entries with an id outside the current beam, a refinement
    below the minimum length, or an id already accepted earlier in the
    same call — the schema asks for one entry per surviving thought
    (the free-text path may carry junk or repeats).  Obstacles are
    coerced to a list of strings.  Shared by the free-text and
    structured paths.
    """
    beam_ids = {t["id"] for t in eligible_thoughts(state)}
    recorded = state.setdefault("expansions", [])
    accepted = 0
    seen: set[int] = set()
    for entry in items:
        if not isinstance(entry, dict):
            continue
        try:
            thought_id = int(entry.get("thought_id"))
        except (TypeError, ValueError):
            logger.warning("Expansion with non-numeric thought_id %r from "
                           "%s, skipping", entry.get("thought_id"),
                           entity.name)
            continue
        if thought_id not in beam_ids:
            logger.warning("Expansion for unknown/pruned thought %s from "
                           "%s, skipping", thought_id, entity.name)
            continue
        if thought_id in seen:
            logger.warning("Duplicate expansion for thought %s from %s in "
                           "one submission, keeping the first accepted",
                           thought_id, entity.name)
            continue
        refinement = str(entry.get("refinement") or "").strip()
        if len(refinement) < MIN_REFINEMENT_LENGTH:
            logger.warning("Expansion with too-short refinement from %s, "
                           "skipping", entity.name)
            continue
        seen.add(thought_id)
        raw_obstacles = entry.get("obstacles")
        obstacles = ([str(o).strip() for o in raw_obstacles
                      if str(o).strip()]
                     if isinstance(raw_obstacles, list) else [])
        recorded.append({
            "depth": depth,
            "entity_id": entity.id,
            "entity_name": entity.name,
            "thought_id": thought_id,
            "refinement": refinement,
            "obstacles": obstacles,
        })
        accepted += 1
    return accepted
