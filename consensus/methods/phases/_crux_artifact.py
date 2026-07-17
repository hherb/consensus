"""Double Crux outcome artifact and display formatting (issue #27).

Split out of ``_crux_helpers.py`` (2026-07-17, keeping that module under
the ~500-line guideline): the deterministic, machine-readable
``crux_map`` artifact and the pure display formatters that render crux
state into prompts.  All functions are pure — they read method state and
return a value with no side effects.  Consumed by the identify, poll,
test, and resolve phase handlers and by ``DoubleCrux`` for the
conclusion prompt.
"""

from __future__ import annotations

from ...evidence import build_evidence_summary
from ._crux_helpers import BELIEF_PRECISION, VERDICT_FACTUAL, VERDICT_NONE


def build_crux_map(state: dict) -> dict:
    """Assemble the machine-readable Double Crux outcome artifact.

    Deterministic (never model-computed): verdict, shared crux,
    positions, submitted cruxes, resolutions, and per-participant
    belief shifts on the shared crux — initial from the belief poll,
    final from resolutions, shift only when both ends are known.
    Caveats flag a missing shared crux, missing resolutions, and a
    factual crux with no computable shift.
    """
    verdict = state.get("crux_verdict", "")
    shared_crux = state.get("shared_crux", {})
    resolutions = state.get("resolutions", [])
    initial = dict(shared_crux.get("initial_beliefs", {}))
    finals = {r["entity_name"]: r["crux_belief"] for r in resolutions
              if r["crux_belief"] is not None}
    shifts: dict[str, dict] = {}
    for name in sorted(set(initial) | set(finals)):
        before = initial.get(name)
        after = finals.get(name)
        shift = (round(after - before, BELIEF_PRECISION)
                 if before is not None and after is not None else None)
        shifts[name] = {"initial": before, "final": after, "shift": shift}
    caveats: list[str] = []
    if verdict == VERDICT_NONE:
        caveats.append(
            "No shared crux was found — the map records the residual "
            "disagreement, not a resolution.")
    if not resolutions:
        caveats.append(
            "No participant resolutions were recorded — final positions "
            "are unknown.")
    if verdict == VERDICT_FACTUAL and not any(
            s["shift"] is not None for s in shifts.values()):
        caveats.append(
            "No belief shift could be computed for the factual crux "
            "(missing initial or final beliefs).")
    return {
        "verdict": verdict,
        "shared_crux": shared_crux,
        "positions": dict(state.get("positions", {})),
        "cruxes": list(state.get("cruxes", [])),
        "resolutions": list(resolutions),
        "belief_shifts": shifts,
        "caveats": caveats,
        "evidence": build_evidence_summary(state),
    }


def format_positions(state: dict) -> str:
    """Participant positions as an indented name → summary list."""
    positions = state.get("positions", {})
    if not positions:
        return "  (No positions were recorded)"
    return "\n".join(f"  {name}: {summary}"
                     for name, summary in positions.items())


def format_cruxes(state: dict) -> str:
    """Numbered crux list with authors and beliefs (identify prompt)."""
    cruxes = state.get("cruxes", [])
    if not cruxes:
        return "  (No cruxes were recorded)"
    lines = []
    for c in cruxes:
        belief = ("unstated" if c["belief"] is None
                  else f"{round(c['belief'], BELIEF_PRECISION)}")
        line = (f"  Crux {c['id']} ({c['entity_name']}, belief {belief}): "
                f"{c['claim']}")
        if c.get("why_pivotal"):
            line += f" — {c['why_pivotal']}"
        lines.append(line)
    return "\n".join(lines)


def format_shared_crux(state: dict) -> str:
    """The identified shared crux (or value difference) as display text."""
    shared = state.get("shared_crux", {})
    if shared.get("claim"):
        return f"  Shared crux: {shared['claim']}"
    if shared.get("description"):
        return f"  Value difference: {shared['description']}"
    return "  (No shared crux was identified)"


def format_belief_shifts(state: dict) -> str:
    """Per-participant initial → final beliefs on the shared crux."""
    shifts = build_crux_map(state)["belief_shifts"]
    if not shifts:
        return "  (No beliefs were recorded)"
    lines = []
    for name, s in shifts.items():
        before = "?" if s["initial"] is None else s["initial"]
        after = "?" if s["final"] is None else s["final"]
        delta = "" if s["shift"] is None else f" (shift {s['shift']:+})"
        lines.append(f"  {name}: {before} → {after}{delta}")
    return "\n".join(lines)


def format_resolutions(state: dict) -> str:
    """Participant resolutions with stances and positions."""
    resolutions = state.get("resolutions", [])
    if not resolutions:
        return "  (No resolutions were recorded)"
    return "\n".join(
        f"  {r['entity_name']} ({r['stance']}): {r['position']}"
        for r in resolutions)
