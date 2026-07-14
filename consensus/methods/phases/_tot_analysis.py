"""Deterministic analysis and formatting for Tree of Thoughts (issue #26).

Composite/beam computation, the machine-readable outcome artifact, and
display formatting — split from ``_tot_helpers`` (recording/validation)
to respect the ~500-line file rule, mirroring the
``_mcda_helpers`` / ``_mcda_analysis`` split.  All numbers (composites,
rankings, the beam, convergence inputs) are computed here in code,
never by the model.
"""

from __future__ import annotations

from ._tot_helpers import (
    BEAM_WIDTH,
    DEFAULT_DIMENSION_SCORE,
    DIMENSIONS,
    MAX_TOT_DEPTH,
    MIN_BEAM_SIZE,
    SCORE_MAX,
    SCORE_MIN,
    STOP_CONVERGED,
    STOP_DEGENERATE,
    STOP_DEPTH,
    eligible_thoughts,
    thought_label,
)

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
    scorer_count) and the top ``BEAM_WIDTH`` ids.  Sort order: scored
    thoughts always rank above unscored ones (an invented default
    composite must never beat real data into the beam), then composite
    descending, then id ascending as the deterministic tie-break.
    """
    composites = thought_composites(state)
    ranking = [{"id": tid, **stats}
               for tid, stats in sorted(
                   composites.items(),
                   key=lambda item: (item[1]["scorer_count"] == 0,
                                     -item[1]["composite"], item[0]))]
    beam_ids = [entry["id"] for entry in ranking[:BEAM_WIDTH]]
    return beam_ids, ranking



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
    # The beam is by construction the ranking's prefix (compute_beam).
    final_beam = [
        {"id": entry["id"],
         "text": by_id.get(entry["id"], {}).get("text", ""),
         "composite": entry["composite"],
         "scorer_count": entry["scorer_count"]}
        for entry in final["ranking"][:len(final["beam_ids"])]
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
# Display formatting
# ----------------------------------------------------------------------

def format_thoughts(thoughts: list[dict]) -> str:
    """Labelled thought list for prompts and transition messages."""
    if not thoughts:
        return "  (No thoughts recorded)"
    return "\n".join(
        f"  {thought_label(t['id'])}: {t['text']}" for t in thoughts)


def format_ranking(state: dict,
                   ranking: list[dict] | None = None) -> str:
    """Current deterministic ranking of the eligible thoughts.

    Pass a precomputed ``ranking`` (from ``compute_beam``) to avoid
    re-aggregating when the caller already has it.
    """
    by_id = {t["id"]: t for t in state.get("thoughts", [])}
    if ranking is None:
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
    """Deep-dive expansions recorded at the given depth.

    Deliberately author-free: the method anonymises authorship end to
    end (approaches are judged on content), so the display names no
    contributor even though ``entity_name`` is recorded in the state.
    """
    entries = [e for e in state.get("expansions", [])
               if e.get("depth") == depth]
    if not entries:
        return "  (No expansions)"
    lines = []
    for e in entries:
        line = (f"  {thought_label(e['thought_id'])}: {e['refinement']}")
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


def latest_expansion_depth(state: dict) -> int | None:
    """Depth of the most recent deep-dive pass, or None if none ran.

    The single source of the "which pass's expansions are current"
    rule — used by the synthesis prompt and the conclusion prompt so
    the tag written by ``record_expansions`` and its inverse cannot
    drift apart.
    """
    depths = [e["depth"] for e in state.get("expansions", [])
              if isinstance(e.get("depth"), int)]
    return max(depths) if depths else None


def format_artifact_digest(state: dict) -> str:
    """Human-readable digest of ``tot_artifact`` for prompts.

    Shared by the synthesis-phase system prompt and the method's
    conclusion prompt so both turns describe the same outcome
    identically.
    """
    artifact = state.get("tot_artifact", {})
    recommendation = artifact.get("recommendation") or {}
    lines = [
        f"Stop reason: {artifact.get('stop_reason') or 'unknown'} after "
        f"{artifact.get('depth', 0)} prune pass(es).",
        "Final beam:",
    ]
    final_beam = artifact.get("final_beam", [])
    if final_beam:
        lines.extend(
            f"  {thought_label(entry['id'])} (composite "
            f"{entry['composite']}, {entry['scorer_count']} scorer(s)): "
            f"{entry['text']}"
            for entry in final_beam)
    else:
        lines.append("  (none)")
    if recommendation:
        lines.append(
            f"Top-ranked approach: {thought_label(recommendation['id'])} "
            f"(composite {recommendation['composite']}): "
            f"{recommendation['text']}")
    lines.append(f"Beam trajectory:\n{format_beam_trajectory(state)}")
    depth = latest_expansion_depth(state)
    if depth is not None:
        lines.append("Deep-dives from the final pass:\n"
                     f"{format_expansions(state, depth)}")
    caveats = artifact.get("caveats", [])
    if caveats:
        lines.append("Caveats:")
        lines.extend(f"  - {caveat}" for caveat in caveats)
    return "\n".join(lines)
