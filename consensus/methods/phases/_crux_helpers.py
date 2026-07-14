"""Shared helpers for Double Crux phase handlers (issue #27).

Pure functions and constants for crux recording/deduplication,
shared-crux selection (verdict + initial-belief snapshot), resolution
recording, free-text extraction fallbacks, the machine-readable
``crux_map`` outcome artifact, and display formatting — used by the
hunt, identify, test, and resolve phase handlers.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ..parsing import extract_json_block, parse_numbered_list, \
    word_overlap_similar
from ...evidence import build_evidence_summary

if TYPE_CHECKING:
    from ...models import Entity

logger = logging.getLogger(__name__)

#: Minimum character length for a claim / position to be substantive.
MIN_CLAIM_LENGTH = 10
#: Word-overlap ratio above which two of the *same entity's* cruxes are
#: considered duplicates (cross-entity overlap is the shared-crux signal).
SIMILARITY_THRESHOLD = 0.7
#: Maximum cruxes a participant may submit in one turn (schema
#: ``maxItems``; also enforced on the free-text path).
MAX_CRUXES_PER_ENTITY = 5
#: Give up on a hunt visit after this many rounds without any cruxes.
MAX_HUNT_ROUNDS = 3
#: Total hunt visits (initial + identify loop-backs) before the verdict
#: is forced to "none" and the method proceeds to resolution.
MAX_CRUX_SEARCH_ROUNDS = 3
#: Give up on moderator crux selection after this many unparseable turns.
MAX_IDENTIFY_ATTEMPTS = 3
#: Give up and conclude after this many resolution rounds.
MAX_RESOLVE_ROUNDS = 3
#: Fixed number of evidence-focused crux-testing rounds.
TEST_CRUX_ROUNDS = 2
#: Decimal places used for belief-shift reporting.
BELIEF_PRECISION = 2

#: Crux-selection verdicts (the ``submit_crux_selection`` enum).
VERDICT_FACTUAL = "factual"
VERDICT_VALUES = "values"
VERDICT_NONE = "none"

#: JSON Schema for the submit_cruxes output tool (issue #23 pattern).
CRUXES_TOOL_PARAMETERS: dict = {
    "type": "object",
    "properties": {
        "cruxes": {
            "type": "array", "minItems": 1,
            "maxItems": MAX_CRUXES_PER_ENTITY,
            "items": {
                "type": "object",
                "properties": {
                    "claim": {
                        "type": "string",
                        "description": ("A specific, checkable factual "
                                        "claim that, if you were wrong "
                                        "about it, would change your mind "
                                        "on the topic."),
                    },
                    "belief": {
                        "type": "number", "minimum": 0, "maximum": 1,
                        "description": ("Your current probability that "
                                        "the claim is true."),
                    },
                    "why_pivotal": {
                        "type": "string",
                        "description": ("Why your position depends on "
                                        "this claim."),
                    },
                },
                "required": ["claim", "belief", "why_pivotal"],
            },
        },
        "reasoning": {
            "type": "string",
            "description": ("How you traced your position back to these "
                            "load-bearing claims."),
        },
    },
    "required": ["cruxes", "reasoning"],
}

#: JSON Schema for the submit_crux_selection output tool (moderator).
CRUX_SELECTION_TOOL_PARAMETERS: dict = {
    "type": "object",
    "properties": {
        "verdict": {
            "type": "string",
            "enum": [VERDICT_FACTUAL, VERDICT_VALUES, VERDICT_NONE],
            "description": ("'factual': a shared factual crux exists; "
                            "'values': the disagreement reduces to a value "
                            "difference; 'none': no shared crux found yet."),
        },
        "crux_ids": {
            "type": "array", "items": {"type": "integer"},
            "description": ("verdict 'factual': ids of the submitted cruxes "
                            "that express the shared crux — ideally from at "
                            "least two different participants."),
        },
        "claim": {
            "type": "string",
            "description": ("verdict 'factual': the shared crux as one "
                            "neutral, checkable claim.  verdict 'values': "
                            "the value difference the disagreement reduces "
                            "to."),
        },
        "reasoning": {"type": "string"},
    },
    "required": ["verdict", "reasoning"],
}

#: JSON Schema for the submit_resolution output tool.
RESOLUTION_TOOL_PARAMETERS: dict = {
    "type": "object",
    "properties": {
        "stance": {
            "type": "string", "enum": ["updated", "unchanged"],
            "description": "Did crux testing change your position?",
        },
        "position": {
            "type": "string",
            "description": "Your current position, stated fully.",
        },
        "crux_belief": {
            "type": "number", "minimum": 0, "maximum": 1,
            "description": ("Your current probability that the shared crux "
                            "claim is true (required when a factual crux "
                            "was tested)."),
        },
        "reasoning": {
            "type": "string",
            "description": ("What moved you, or why the evidence did not "
                            "move you."),
        },
    },
    "required": ["stance", "position", "reasoning"],
}


def _belief_error(value: object) -> str:
    """Return '' when a belief value is a number in [0, 1], else an error."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "'belief' values must be numbers between 0 and 1."
    if not 0 <= float(value) <= 1:
        return "'belief' values must be between 0 and 1."
    return ""


def validate_cruxes_payload(payload: dict) -> str:
    """Return '' if a submit_cruxes payload is usable, else an error."""
    cruxes = payload.get("cruxes")
    if not isinstance(cruxes, list) or not cruxes:
        return "'cruxes' must be a non-empty array of crux objects."
    if len(cruxes) > MAX_CRUXES_PER_ENTITY:
        return (f"Submit at most {MAX_CRUXES_PER_ENTITY} cruxes — pick "
                "the claims your position genuinely rests on.")
    for crux in cruxes:
        if not isinstance(crux, dict):
            return "Each entry in 'cruxes' must be an object."
        claim = crux.get("claim")
        if not isinstance(claim, str) or len(claim.strip()) < MIN_CLAIM_LENGTH:
            return ("Each 'claim' must be a specific, checkable statement "
                    f"of at least {MIN_CLAIM_LENGTH} characters "
                    f"(got: {claim!r}).")
        error = _belief_error(crux.get("belief"))
        if error:
            return error
        if not str(crux.get("why_pivotal") or "").strip():
            return ("Each crux needs a 'why_pivotal' explaining why your "
                    "position depends on the claim.")
    if not str(payload.get("reasoning") or "").strip():
        return "'reasoning' must explain how you traced your cruxes."
    return ""


def record_cruxes(state: dict, entity: Entity,
                  items: list[dict]) -> list[dict]:
    """Dedup (per entity), id, and append cruxes; return accepted dicts.

    A crux is dropped when its claim is word-overlap similar to a claim
    the *same entity* already submitted — similar claims from different
    entities are kept, because cross-party overlap is exactly the
    shared-crux signal the identify phase looks for.  Beliefs are
    float-coerced and clamped to [0, 1]; ``None`` (the free-text path)
    is preserved.  At most ``MAX_CRUXES_PER_ENTITY`` items are taken
    per call, so the free-text path honours the same per-turn bound
    the schema puts on the tool path.  Shared by both paths.
    """
    cruxes = state.setdefault("cruxes", [])
    accepted: list[dict] = []
    for item in items[:MAX_CRUXES_PER_ENTITY]:
        claim = str(item.get("claim") or "").strip().rstrip('.')
        if len(claim) < MIN_CLAIM_LENGTH:
            continue
        if any(c["entity_id"] == entity.id
               and word_overlap_similar(claim, c["claim"],
                                        threshold=SIMILARITY_THRESHOLD)
               for c in cruxes):
            continue
        raw_belief = item.get("belief")
        belief: float | None
        if raw_belief is None or isinstance(raw_belief, bool):
            belief = None
        else:
            try:
                belief = min(1.0, max(0.0, float(raw_belief)))
            except (TypeError, ValueError):
                belief = None
        crux = {
            "id": len(cruxes) + 1,
            "entity_id": entity.id,
            "entity_name": entity.name,
            "claim": claim,
            "belief": belief,
            "why_pivotal": str(item.get("why_pivotal") or "").strip(),
        }
        cruxes.append(crux)
        accepted.append(crux)
    return accepted


def extract_cruxes(content: str) -> list[dict]:
    """Parse cruxes from free text (human/fallback path).

    Tries a fenced JSON block with a ``cruxes`` array first, then a
    numbered list of claims (belief unknown → ``None``).
    """
    data = extract_json_block(content)
    if isinstance(data, dict) and isinstance(data.get("cruxes"), list):
        return [c for c in data["cruxes"] if isinstance(c, dict)]
    return [{"claim": text, "belief": None, "why_pivotal": ""}
            for text in parse_numbered_list(content,
                                            min_length=MIN_CLAIM_LENGTH)]


def validate_crux_selection_payload(payload: dict,
                                    valid_ids: set[int]) -> str:
    """Return '' if a submit_crux_selection payload is usable, else an error."""
    verdict = payload.get("verdict")
    if verdict not in (VERDICT_FACTUAL, VERDICT_VALUES, VERDICT_NONE):
        return ("'verdict' must be one of 'factual', 'values', or 'none' "
                f"(got: {verdict!r}).")
    if verdict in (VERDICT_FACTUAL, VERDICT_VALUES):
        claim = payload.get("claim")
        if not isinstance(claim, str) or len(claim.strip()) < MIN_CLAIM_LENGTH:
            what = ("the shared crux claim" if verdict == VERDICT_FACTUAL
                    else "the value difference")
            return (f"'claim' must state {what} in at least "
                    f"{MIN_CLAIM_LENGTH} characters.")
    if verdict == VERDICT_FACTUAL:
        crux_ids = payload.get("crux_ids")
        if not isinstance(crux_ids, list) or not crux_ids:
            return ("verdict 'factual' requires 'crux_ids' — the ids of the "
                    "submitted cruxes that express the shared crux.")
        for cid in crux_ids:
            if isinstance(cid, bool) or not isinstance(cid, int):
                return "'crux_ids' must be an array of integers."
            if cid not in valid_ids:
                return (f"Crux {cid} does not exist. Valid crux ids: "
                        f"{sorted(valid_ids)}.")
    if not str(payload.get("reasoning") or "").strip():
        return "'reasoning' must explain your verdict."
    return ""


def record_crux_selection(state: dict, payload: dict) -> None:
    """Record the moderator's crux verdict into method state.

    Sets ``crux_verdict`` and ``shared_crux``.  For a factual verdict,
    snapshots each referenced participant's stated belief on their
    source crux into ``initial_beliefs`` (the "before" end of the
    belief-shift metric); ``None`` beliefs are skipped.
    """
    verdict = payload["verdict"]
    state["crux_verdict"] = verdict
    if verdict == VERDICT_FACTUAL:
        crux_ids = [int(cid) for cid in payload.get("crux_ids", [])]
        by_id = {c["id"]: c for c in state.get("cruxes", [])}
        initial_beliefs: dict[str, float] = {}
        for cid in crux_ids:
            crux = by_id.get(cid)
            if crux is not None and crux["belief"] is not None:
                initial_beliefs[crux["entity_name"]] = crux["belief"]
        state["shared_crux"] = {
            "claim": str(payload.get("claim") or "").strip().rstrip('.'),
            "description": "",
            "source_crux_ids": crux_ids,
            "initial_beliefs": initial_beliefs,
        }
    elif verdict == VERDICT_VALUES:
        state["shared_crux"] = {
            "claim": "",
            "description": str(payload.get("claim") or "").strip(),
            "source_crux_ids": [],
            "initial_beliefs": {},
        }
    else:
        state["shared_crux"] = {}


def extract_crux_selection(content: str) -> dict | None:
    """Parse a crux selection from free text (fallback path).

    Only a fenced JSON block with a ``verdict`` key is accepted — the
    verdict routing is too consequential for looser heuristics.
    """
    data = extract_json_block(content)
    if isinstance(data, dict) and "verdict" in data:
        return data
    return None


def validate_resolution_payload(payload: dict, require_belief: bool) -> str:
    """Return '' if a submit_resolution payload is usable, else an error.

    Args:
        payload: The candidate resolution payload.
        require_belief: True when a factual crux was tested — the
            participant must then restate their probability on it.
    """
    if payload.get("stance") not in ("updated", "unchanged"):
        return "'stance' must be 'updated' or 'unchanged'."
    position = payload.get("position")
    if not isinstance(position, str) or len(position.strip()) < MIN_CLAIM_LENGTH:
        return ("'position' must state your current position in at least "
                f"{MIN_CLAIM_LENGTH} characters.")
    belief = payload.get("crux_belief")
    if belief is None:
        if require_belief:
            return ("'crux_belief' is required: state your current "
                    "probability (0-1) that the shared crux claim is true.")
    else:
        error = _belief_error(belief)
        if error:
            return error.replace("'belief'", "'crux_belief'")
    if not str(payload.get("reasoning") or "").strip():
        return ("'reasoning' must explain what moved you, or why the "
                "evidence did not.")
    return ""


def record_resolution(state: dict, entity: Entity, payload: dict) -> None:
    """Record an entity's resolution; resubmission replaces their own.

    Shared by the free-text and structured paths.
    """
    belief = payload.get("crux_belief")
    resolution = {
        "entity_id": entity.id,
        "entity_name": entity.name,
        "stance": payload["stance"],
        "position": str(payload.get("position") or "").strip(),
        "crux_belief": None if belief is None else float(belief),
        "reasoning": str(payload.get("reasoning") or "").strip(),
    }
    resolutions = state.setdefault("resolutions", [])
    for i, existing in enumerate(resolutions):
        if existing["entity_id"] == entity.id:
            resolutions[i] = resolution
            return
    resolutions.append(resolution)


def extract_resolution(content: str) -> dict | None:
    """Parse a resolution from free text (fallback path).

    Only a fenced JSON block with a ``stance`` key is accepted.
    """
    data = extract_json_block(content)
    if isinstance(data, dict) and "stance" in data:
        return data
    return None


def entities_with_resolutions(state: dict) -> set[int]:
    """Entity ids that have a recorded resolution."""
    return {r["entity_id"] for r in state.get("resolutions", [])}


def build_crux_map(state: dict) -> dict:
    """Assemble the machine-readable Double Crux outcome artifact.

    Deterministic (never model-computed): verdict, shared crux,
    positions, submitted cruxes, resolutions, and per-participant
    belief shifts on the shared crux — initial from the hunt-phase
    snapshot, final from resolutions, shift only when both ends are
    known.  Caveats flag a missing shared crux, missing resolutions,
    and a factual crux with no computable shift.
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
