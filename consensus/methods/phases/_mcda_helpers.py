"""Shared helpers for Weighted Decision Matrix (MCDA) phase handlers (issue #25).

Pure functions and constants for option/criterion recording and
deduplication, score sanitisation, free-text extraction, aggregation
math (weighted totals, per-participant divergence, one-at-a-time
sensitivity), decision-artifact assembly, and display formatting —
used by the options, criteria, score, sensitivity, and decide phase
handlers.
"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING

from ..parsing import extract_json_block, parse_numbered_list, word_overlap_similar

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
    """Dedup, id, and append options; return the accepted option dicts.

    An option is dropped when it is word-overlap similar to an option
    already recorded.  Shared by the free-text and structured-output
    paths (issue #23).
    """
    options = state.setdefault("options", [])
    accepted: list[dict] = []
    for text in texts:
        cleaned = str(text).strip().rstrip('.')
        if len(cleaned) < MIN_OPTION_LENGTH:
            continue
        if any(word_overlap_similar(cleaned, existing["text"],
                                    threshold=SIMILARITY_THRESHOLD)
               for existing in options):
            continue
        option = {
            "id": len(options) + 1,
            "entity_id": entity.id,
            "entity_name": entity.name,
            "text": cleaned,
        }
        options.append(option)
        accepted.append(option)
    return accepted


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

    Keeps only integer-coercible, in-range cells (the free-text path
    may carry quoted numbers or junk; booleans are rejected — bool is
    an int subtype and ``True`` would silently count as a score of 1).
    Returns the number of cells kept; records nothing when no cell
    survives.  Shared by the free-text and structured paths.
    """
    cleaned: dict[str, dict[str, int]] = {}
    kept = 0
    for okey, row in scores.items():
        if not isinstance(row, dict):
            continue
        for ckey, value in row.items():
            if isinstance(value, bool):
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


# ---------------------------------------------------------------------------
# Aggregation & analysis
# ---------------------------------------------------------------------------

def mean_scores(state: dict) -> dict[str, dict[str, float]]:
    """Mean score per option × criterion cell across all scorers.

    A cell nobody scored defaults to ``DEFAULT_SCORE`` (the scale
    midpoint) so partial coverage never zeroes an option out.
    """
    all_scores = state.get("scores", {})
    means: dict[str, dict[str, float]] = {}
    for option in state.get("options", []):
        okey = option_label(option["id"])
        means[okey] = {}
        for crit in state.get("criteria", []):
            ckey = criterion_label(crit["id"])
            values = [entity_scores[okey][ckey]
                      for entity_scores in all_scores.values()
                      if ckey in entity_scores.get(okey, {})]
            means[okey][ckey] = (sum(values) / len(values) if values
                                 else float(DEFAULT_SCORE))
    return means


def _weighted_totals_from_means(
        state: dict, means: dict[str, dict[str, float]], *,
        override_id: int | None = None,
        override_weight: float = 0.0) -> dict[int, float]:
    """Weighted total per option id, optionally overriding one weight.

    The override is how the sensitivity analysis asks "what if this
    criterion's weight were X?" without mutating state.
    """
    totals: dict[int, float] = {}
    for option in state.get("options", []):
        okey = option_label(option["id"])
        total = 0.0
        for crit in state.get("criteria", []):
            weight = (override_weight if crit["id"] == override_id
                      else criterion_weight(crit))
            total += weight * means[okey][criterion_label(crit["id"])]
        totals[option["id"]] = total
    return totals


def weighted_totals(state: dict) -> dict[int, float]:
    """Weighted total per option id: Σ (mean weight × mean score)."""
    return _weighted_totals_from_means(state, mean_scores(state))


def participant_totals(state: dict) -> dict[str, dict[int, float]]:
    """Weighted total per option using each participant's own scores.

    Group (mean) weights are used throughout; a participant's unscored
    cell defaults to ``DEFAULT_SCORE``.  Keyed by ``str(entity_id)``
    (the ``scores`` key shape).
    """
    per: dict[str, dict[int, float]] = {}
    for entity_id, entity_scores in state.get("scores", {}).items():
        totals: dict[int, float] = {}
        for option in state.get("options", []):
            okey = option_label(option["id"])
            total = 0.0
            for crit in state.get("criteria", []):
                ckey = criterion_label(crit["id"])
                score = entity_scores.get(okey, {}).get(ckey, DEFAULT_SCORE)
                total += criterion_weight(crit) * score
            totals[option["id"]] = total
        per[entity_id] = totals
    return per


def divergence_by_option(state: dict) -> dict[int, float]:
    """Spread (max − min) of participant weighted totals per option.

    0.0 when fewer than two participants scored — there is no spread
    to report.
    """
    per = participant_totals(state)
    spreads: dict[int, float] = {}
    for option in state.get("options", []):
        values = [totals[option["id"]] for totals in per.values()]
        spreads[option["id"]] = (max(values) - min(values)
                                 if len(values) >= 2 else 0.0)
    return spreads


def ranked_options(state: dict) -> list[dict]:
    """Options sorted by weighted total (descending; ties by lower id)."""
    totals = weighted_totals(state)
    return [{"id": o["id"], "text": o["text"], "total": totals[o["id"]]}
            for o in sorted(state.get("options", []),
                            key=lambda o: (-totals[o["id"]], o["id"]))]


#: One-at-a-time weight variations tried per criterion: the criterion
#: excluded entirely, and its weight doubled.
SENSITIVITY_VARIATIONS: tuple[tuple[str, float], ...] = (
    ("excluded", 0.0),
    ("doubled", 2.0),
)


def sensitivity_report(state: dict) -> dict:
    """One-at-a-time sensitivity analysis of the weighted ranking.

    For each criterion, recomputes the winner with that criterion
    excluded (weight 0) and with its weight doubled; a variation that
    flips the winner marks the criterion pivotal.  Also flags a close
    call when the top-two margin is within ``CLOSE_CALL_MARGIN`` of
    the top total.  JSON-serialisable.
    """
    ranking = ranked_options(state)
    report: dict = {
        "baseline_winner_id": ranking[0]["id"] if ranking else None,
        "close_call": False,
        "margin": 0.0,
        "pivotal_criteria": [],
    }
    if len(ranking) < 2:
        return report
    top, second = ranking[0], ranking[1]
    margin = top["total"] - second["total"]
    report["margin"] = round(margin, 2)
    report["close_call"] = (top["total"] > 0
                            and margin / top["total"] <= CLOSE_CALL_MARGIN)
    means = mean_scores(state)
    texts = {o["id"]: o["text"] for o in state.get("options", [])}
    for crit in state.get("criteria", []):
        base_weight = criterion_weight(crit)
        for variation, factor in SENSITIVITY_VARIATIONS:
            totals = _weighted_totals_from_means(
                state, means, override_id=crit["id"],
                override_weight=base_weight * factor)
            winner_id = min(totals, key=lambda oid: (-totals[oid], oid))
            if winner_id != top["id"]:
                report["pivotal_criteria"].append({
                    "criterion_id": crit["id"],
                    "name": crit["name"],
                    "variation": variation,
                    "new_winner_id": winner_id,
                    "new_winner": texts.get(winner_id, ""),
                })
    return report


def build_decision_artifact(state: dict, recommended_option_id: int,
                            rationale: str, caveats: list[str]) -> dict:
    """Assemble and store the machine-readable decision artifact.

    The artifact (``state["decision_artifact"]``) is the method's
    structured final output (issue #25): ranked options with weighted
    totals and per-criterion mean scores, effective criteria weights,
    per-participant divergence, the sensitivity report, and the
    recorded recommendation.  Consumable by the storyboard, the MCP
    server, or a follow-up discussion; fully JSON-serialisable.
    """
    means = mean_scores(state)
    options_by_id = {o["id"]: o for o in state.get("options", [])}
    artifact = {
        "method": "decision_matrix",
        "criteria": [{"id": c["id"], "name": c["name"],
                      "weight": round(criterion_weight(c), 2)}
                     for c in state.get("criteria", [])],
        "ranking": [
            {"option_id": r["id"], "text": r["text"],
             "weighted_total": round(r["total"], 2),
             "mean_scores": {
                 criterion_label(c["id"]): round(
                     means[option_label(r["id"])][criterion_label(c["id"])],
                     2)
                 for c in state.get("criteria", [])}}
            for r in ranked_options(state)
        ],
        "divergence": [{"option_id": oid, "spread": round(spread, 2)}
                       for oid, spread
                       in sorted(divergence_by_option(state).items())],
        "sensitivity": sensitivity_report(state),
        "scorers": len(state.get("scores", {})),
        "recommended_option_id": recommended_option_id,
        "recommended_option": options_by_id.get(
            recommended_option_id, {}).get("text", ""),
        "rationale": rationale,
        "caveats": list(caveats),
    }
    state["decision_artifact"] = artifact
    return artifact


# ---------------------------------------------------------------------------
# Display formatting
# ---------------------------------------------------------------------------

def format_options(state: dict) -> str:
    """Option list with O-labels for prompts and tool descriptions."""
    options = state.get("options", [])
    if not options:
        return "  (No options)"
    return "\n".join(f"  O{o['id']}: {o['text']}" for o in options)


def format_criteria(state: dict) -> str:
    """Criteria list with effective weights and vote counts."""
    criteria = state.get("criteria", [])
    if not criteria:
        return "  (No criteria)"
    return "\n".join(
        f"  C{c['id']}: {c['name']} — weight {criterion_weight(c):.1f} "
        f"({len(c.get('weight_votes', {}))} vote(s))"
        for c in criteria)


def format_score_table(scores: dict, state: dict) -> str:
    """Render any option×criterion mapping as a markdown table.

    Works for a participant's integer scores and for the float mean
    matrix alike; missing cells render as ``?``.
    """
    options = state.get("options", [])
    criteria = state.get("criteria", [])
    if not options or not criteria:
        return ""
    header = " | ".join(f"C{c['id']} ({c['name']})" for c in criteria)
    lines = [f"| Option | {header} |",
             f"|---|{'---|' * len(criteria)}"]
    for o in options:
        row = scores.get(option_label(o["id"]), {})
        cells = []
        for c in criteria:
            value = row.get(criterion_label(c["id"]), "?")
            cells.append(f"{value:g}" if isinstance(value, float)
                         else str(value))
        lines.append(f"| **O{o['id']}** {o['text']} | "
                     + " | ".join(cells) + " |")
    return "\n".join(lines)


def format_mean_score_matrix(state: dict) -> str:
    """The aggregated (mean) score matrix as a markdown table."""
    return format_score_table(mean_scores(state), state)


def format_weighted_ranking(state: dict) -> str:
    """Options ranked by weighted total, one per line."""
    ranking = ranked_options(state)
    if not ranking:
        return "  (No options)"
    return "\n".join(
        f"  {rank}. O{r['id']}: {r['text']} — "
        f"weighted total {r['total']:.1f}"
        for rank, r in enumerate(ranking, 1))


def format_divergence(state: dict) -> str:
    """Per-option spread of participant totals, one per line."""
    spreads = divergence_by_option(state)
    texts = {o["id"]: o["text"] for o in state.get("options", [])}
    if not spreads:
        return "  (No options)"
    return "\n".join(
        f"  O{oid}: {texts[oid]} — spread {spread:.1f}"
        for oid, spread in sorted(spreads.items()))


def format_sensitivity(state: dict) -> str:
    """Human-readable sensitivity findings for the moderator prompt."""
    report = sensitivity_report(state)
    lines: list[str] = []
    if report["close_call"]:
        lines.append(
            f"  ⚠ CLOSE CALL: the top two options are within "
            f"{report['margin']:.1f} points.")
    for p in report["pivotal_criteria"]:
        lines.append(
            f"  Pivotal: with C{p['criterion_id']} ({p['name']}) "
            f"{p['variation']}, the winner changes to "
            f"O{p['new_winner_id']}: {p['new_winner']}.")
    if not lines:
        lines.append(
            "  The ranking is robust: no single weight change flips "
            "the winner, and the margin is not close.")
    return "\n".join(lines)


def format_decision_artifact(artifact: dict) -> str:
    """Render the decision artifact for the transcript and conclusion."""
    lines = [f"**Decision: {artifact.get('recommended_option', '?')}**", ""]
    rationale = artifact.get("rationale", "")
    if rationale:
        lines += [rationale, ""]
    lines.append("Ranked options:")
    for rank, r in enumerate(artifact.get("ranking", []), 1):
        lines.append(
            f"  {rank}. O{r['option_id']}: {r['text']} — "
            f"weighted total {r['weighted_total']:.1f}")
    caveats = artifact.get("caveats") or []
    if caveats:
        lines += ["", "Caveats:"]
        lines += [f"  - {c}" for c in caveats]
    return "\n".join(lines)
