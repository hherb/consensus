"""Aggregation, sensitivity, and artifact helpers for MCDA (issue #25).

The numeric-analysis and display-formatting layer of the Weighted
Decision Matrix method: mean-score aggregation, weighted totals,
per-participant divergence, one-at-a-time sensitivity analysis,
decision-artifact assembly, and the markdown formatters used in
prompts and transcripts.  Recording/validation lives in
``_mcda_helpers.py``.
"""

from __future__ import annotations

from ._mcda_helpers import (
    CLOSE_CALL_MARGIN,
    DEFAULT_SCORE,
    criterion_label,
    criterion_weight,
    option_label,
)

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
    caveats = list(caveats)
    if not state.get("scores"):
        caveats.append(
            "No participant scores were recorded — every cell defaulted "
            "to the scale midpoint, so the weighted ranking does not "
            "differentiate the options.")
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
        "caveats": caveats,
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
    matrix alike; float cells are rounded to two decimals (the
    artifact's precision) and missing cells render as ``?``.
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
            cells.append(f"{round(value, 2):g}" if isinstance(value, float)
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
