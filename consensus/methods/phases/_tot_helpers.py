"""Shared helpers for Tree of Thoughts phase handlers (issue #26).

Pure functions and constants for thought recording/deduplication,
fixed-dimension (feasibility/impact/risk) score validation and
recording, deterministic composite/beam computation, expansion
recording, the machine-readable outcome artifact, JSON extraction for
the free-text fallback paths, and display formatting — used by the
propose, score, prune, expand, and synthesise phase handlers.

All numbers (composites, rankings, the beam, convergence) are computed
here in code, never by the model — the MCDA sensitivity / Double Crux
belief-shift convention.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Optional, Union

from ..parsing import extract_json_block, word_overlap_similar

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
    thoughts = payload.get("thoughts")
    if not isinstance(thoughts, list) or not thoughts:
        return "'thoughts' must be a non-empty array of approach strings."
    for thought in thoughts:
        if (not isinstance(thought, str)
                or len(thought.strip()) < MIN_THOUGHT_LENGTH):
            return ("Each thought must be a complete, specific approach of "
                    f"at least {MIN_THOUGHT_LENGTH} characters "
                    f"(got: {thought!r}).")
    if not str(payload.get("reasoning") or "").strip():
        return "'reasoning' must contain your rationale for these approaches."
    return ""


def record_thoughts(state: dict, entity: Entity,
                    texts: list[str]) -> list[dict]:
    """Dedup, id, and append thoughts; return the accepted thought dicts.

    A thought is dropped when it is word-overlap similar to any thought
    already recorded — cross-entity near-duplicates carry no signal
    here (unlike Double Crux cruxes), and scoring + pruning filters
    whatever survives this coarse gate.  Shared by the free-text and
    structured-output paths (issue #23).
    """
    thoughts = state.setdefault("thoughts", [])
    accepted: list[dict] = []
    for text in texts:
        cleaned = str(text).strip().rstrip('.')
        if len(cleaned) < MIN_THOUGHT_LENGTH:
            continue
        if any(word_overlap_similar(cleaned, existing["text"],
                                    threshold=SIMILARITY_THRESHOLD)
               for existing in thoughts):
            continue
        thought = {
            "id": len(thoughts) + 1,
            "entity_id": entity.id,
            "entity_name": entity.name,
            "text": cleaned,
        }
        thoughts.append(thought)
        accepted.append(thought)
    return accepted


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
    eligibility-scoped aggregation.  Returns the number of thought
    entries kept.  Shared by the free-text and structured paths.
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
    return kept


# ----------------------------------------------------------------------
# Composite / beam computation (deterministic — never the model)
# ----------------------------------------------------------------------

def composite_of(dims: dict[str, int]) -> int:
    """Composite of one scorer's entry: feasibility + impact − risk.

    Risk is inverted onto the same scale
    (``SCORE_MIN + SCORE_MAX − risk``) so higher composites are always
    better.
    """
    return (dims["feasibility"] + dims["impact"]
            + (SCORE_MIN + SCORE_MAX - dims["risk"]))


def thought_composites(state: dict) -> dict[int, dict]:
    """Mean composite and scorer count per eligible thought id.

    A thought nobody scored gets the all-midpoint composite (neutral)
    with ``scorer_count`` 0 — flagged as a caveat in the artifact.
    """
    default = float(composite_of(
        {dim: DEFAULT_DIMENSION_SCORE for dim in DIMENSIONS}))
    result: dict[int, dict] = {}
    all_scores = state.get("thought_scores", {})
    for thought in eligible_thoughts(state):
        label = thought_label(thought["id"])
        composites = [composite_of(entry[label])
                      for entry in all_scores.values() if label in entry]
        if composites:
            mean = round(sum(composites) / len(composites), 2)
        else:
            mean = default
        result[thought["id"]] = {"composite": mean,
                                 "scorer_count": len(composites)}
    return result


def compute_beam(state: dict) -> tuple[list[int], list[dict]]:
    """Rank eligible thoughts and cut the beam.

    Returns ``(beam_ids, ranking)``: the full ranking (id, composite,
    scorer_count) sorted by composite descending with id ascending as
    the deterministic tie-break, and the top ``BEAM_WIDTH`` ids.
    """
    composites = thought_composites(state)
    ranking = [{"id": tid, **stats}
               for tid, stats in sorted(
                   composites.items(),
                   key=lambda item: (-item[1]["composite"], item[0]))]
    beam_ids = [entry["id"] for entry in ranking[:BEAM_WIDTH]]
    return beam_ids, ranking


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
    for entry in expansions:
        if not isinstance(entry, dict):
            return "Each entry in 'expansions' must be an object."
        raw_id = entry.get("thought_id")
        if isinstance(raw_id, bool) or not isinstance(raw_id, int):
            return "'thought_id' must be an integer thought id."
        if raw_id not in beam_ids:
            return (f"Thought {raw_id} is not in the surviving beam. "
                    f"Valid thought ids: {sorted(beam_ids)}.")
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

    Skips entries with an id outside the current beam or a refinement
    below the minimum length (the free-text path may carry junk).
    Obstacles are coerced to a list of strings.  Shared by the
    free-text and structured paths.
    """
    beam_ids = {t["id"] for t in eligible_thoughts(state)}
    recorded = state.setdefault("expansions", [])
    accepted = 0
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
        refinement = str(entry.get("refinement") or "").strip()
        if len(refinement) < MIN_REFINEMENT_LENGTH:
            logger.warning("Expansion with too-short refinement from %s, "
                           "skipping", entity.name)
            continue
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


# ----------------------------------------------------------------------
# Outcome artifact
# ----------------------------------------------------------------------

def build_tot_artifact(state: dict, stop_reason: str) -> dict:
    """Build the machine-readable outcome artifact (issue #26).

    Mirrors MCDA's ``decision_artifact`` / Double Crux's ``crux_map``:
    every number comes from the deterministic beam computation, never
    from the model.  Assumes the final beam has already been appended
    to ``beam_history``.
    """
    history = state.get("beam_history", [])
    final = history[-1] if history else {"beam_ids": [], "ranking": []}
    by_id = {t["id"]: t for t in state.get("thoughts", [])}
    final_beam = [
        {"id": entry["id"],
         "text": by_id.get(entry["id"], {}).get("text", ""),
         "composite": entry["composite"],
         "scorer_count": entry["scorer_count"]}
        for entry in final["ranking"]
        if entry["id"] in set(final["beam_ids"])
    ]
    recommendation = (
        {"id": final_beam[0]["id"], "text": final_beam[0]["text"],
         "composite": final_beam[0]["composite"]}
        if final_beam else {})
    caveats: list[str] = []
    if not state.get("thought_scores"):
        caveats.append("No participant submitted scores — the ranking is "
                       "contentless (all composites defaulted).")
    else:
        unscored = [thought_label(b["id"]) for b in final_beam
                    if b["scorer_count"] == 0]
        if unscored:
            caveats.append("Surviving thought(s) "
                           f"{', '.join(unscored)} were never scored and "
                           "carry the neutral default composite.")
    if stop_reason == STOP_DEPTH:
        caveats.append(f"The depth budget ({MAX_TOT_DEPTH} passes) was "
                       "spent before the beam stabilised — further "
                       "iterations might still reorder the survivors.")
    elif stop_reason == STOP_DEGENERATE:
        caveats.append("Fewer than the minimum beam of "
                       f"{MIN_BEAM_SIZE} thoughts survived — there was "
                       "nothing to explore in parallel.")
    return {
        "recommendation": recommendation,
        "converged": stop_reason == STOP_CONVERGED,
        "stop_reason": stop_reason,
        "depth": len(history),
        "final_beam": final_beam,
        "beam_history": list(history),
        "expansions": list(state.get("expansions", [])),
        "caveats": caveats,
    }


# ----------------------------------------------------------------------
# Free-text extraction (human/fallback path)
# ----------------------------------------------------------------------

def extract_json_payload(content: str,
                         key: str) -> Optional[Union[dict, list]]:
    """Extract the value of ``key`` from JSON embedded in free text.

    Tries a fenced JSON block first, then an inline (unfenced) object
    scanned to its balanced closing brace — a lazy regex would truncate
    at the first inner brace (the ``extract_scores`` pattern from
    ``_mcda_helpers``, generalised over the key).
    """
    data = extract_json_block(content)
    if isinstance(data, dict) and isinstance(data.get(key), (dict, list)):
        return data[key]
    start = content.find(f'{{"{key}"')
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
                        value = data.get(key)
                        return value if isinstance(value,
                                                   (dict, list)) else None
                    except (json.JSONDecodeError, ValueError):
                        pass
                    break
    return None


# ----------------------------------------------------------------------
# Display formatting
# ----------------------------------------------------------------------

def format_thoughts(thoughts: list[dict]) -> str:
    """Labelled thought list for prompts and transition messages."""
    if not thoughts:
        return "  (No thoughts recorded)"
    return "\n".join(
        f"  {thought_label(t['id'])}: {t['text']}" for t in thoughts)


def format_ranking(state: dict) -> str:
    """Current deterministic ranking of the eligible thoughts."""
    by_id = {t["id"]: t for t in state.get("thoughts", [])}
    _, ranking = compute_beam(state)
    if not ranking:
        return "  (Nothing to rank)"
    return "\n".join(
        f"  {pos}. {thought_label(e['id'])} — composite "
        f"{e['composite']} from {e['scorer_count']} scorer(s): "
        f"{by_id.get(e['id'], {}).get('text', '')}"
        for pos, e in enumerate(ranking, 1))


def format_beam(state: dict) -> str:
    """The latest recorded beam (survivors) with composites."""
    history = state.get("beam_history", [])
    if not history:
        return "  (No beam recorded)"
    latest = history[-1]
    by_id = {t["id"]: t for t in state.get("thoughts", [])}
    kept = {e["id"]: e for e in latest.get("ranking", [])}
    lines = []
    for tid in latest.get("beam_ids", []):
        entry = kept.get(tid, {})
        lines.append(
            f"  {thought_label(tid)} (composite "
            f"{entry.get('composite', '?')}): "
            f"{by_id.get(tid, {}).get('text', '')}")
    return "\n".join(lines) if lines else "  (Empty beam)"


def format_expansions(state: dict, depth: int) -> str:
    """Deep-dive expansions recorded at the given depth."""
    entries = [e for e in state.get("expansions", [])
               if e.get("depth") == depth]
    if not entries:
        return "  (No expansions)"
    lines = []
    for e in entries:
        line = (f"  {thought_label(e['thought_id'])} — {e['entity_name']}: "
                f"{e['refinement']}")
        if e.get("obstacles"):
            line += f" [obstacles: {'; '.join(e['obstacles'])}]"
        lines.append(line)
    return "\n".join(lines)


def format_beam_trajectory(state: dict) -> str:
    """Beam evolution across prune passes, for the synthesis phase."""
    history = state.get("beam_history", [])
    if not history:
        return "  (No prune passes recorded)"
    lines = []
    for entry in history:
        labels = ", ".join(thought_label(tid)
                           for tid in entry.get("beam_ids", []))
        lines.append(f"  Pass {entry.get('depth', '?')}: beam = "
                     f"{labels or '(empty)'}")
    return "\n".join(lines)
