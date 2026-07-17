"""Shared helpers for Double Crux phase handlers (issue #27).

Pure functions and constants for crux recording/deduplication,
shared-crux selection (verdict + shared-claim capture), belief-poll
recording (``record_poll_belief`` / ``apply_poll_beliefs``, the source
of ``initial_beliefs``), poll-context redaction, resolution recording,
and free-text extraction fallbacks — used by the hunt, identify, poll,
test, and resolve phase handlers.  The ``crux_map`` outcome artifact and
the display formatters live alongside in ``_crux_artifact.py``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ..parsing import extract_json_block, parse_numbered_list, \
    word_overlap_similar

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
#: Give up and advance after this many belief-poll rounds.
MAX_POLL_ROUNDS = 3
#: Fixed number of evidence-focused crux-testing rounds.
TEST_CRUX_ROUNDS = 2
#: Decimal places used for belief-shift reporting.
BELIEF_PRECISION = 2
#: Prefix of the rendered line that carries a participant's numeric
#: belief on the shared crux.  Rendered on the poll/resolution turn and
#: matched to redact that number from other pollers' context, keeping the
#: belief poll a clean, non-anchored baseline (design 2026-07-17).
BELIEF_LINE_PREFIX = "Belief on the crux:"

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

#: JSON Schema for the submit_crux_belief output tool (belief poll).
POLL_BELIEF_TOOL_PARAMETERS: dict = {
    "type": "object",
    "properties": {
        "belief": {
            "type": "number", "minimum": 0, "maximum": 1,
            "description": ("Your current probability (0-1) that the "
                            "shared crux claim is true, before evidence "
                            "is presented."),
        },
        "reasoning": {
            "type": "string",
            "description": ("Why you hold that probability right now."),
        },
    },
    "required": ["belief", "reasoning"],
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
    records the shared claim and source crux ids; ``initial_beliefs``
    is left empty for the poll_belief phase to fill.
    """
    verdict = payload["verdict"]
    state["crux_verdict"] = verdict
    if verdict == VERDICT_FACTUAL:
        crux_ids = [int(cid) for cid in payload.get("crux_ids", [])]
        # initial_beliefs is owned by the poll_belief phase (design
        # 2026-07-17): it is polled on this shared claim for every party,
        # not snapshotted from the (differently-phrased) hunt cruxes.
        state["shared_crux"] = {
            "claim": str(payload.get("claim") or "").strip().rstrip('.'),
            "description": "",
            "source_crux_ids": crux_ids,
            "initial_beliefs": {},
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


def validate_poll_belief_payload(payload: dict) -> str:
    """Return '' if a submit_crux_belief payload is usable, else an error."""
    error = _belief_error(payload.get("belief"))
    if error:
        return error
    if not str(payload.get("reasoning") or "").strip():
        return "'reasoning' must explain your current probability."
    return ""


def record_poll_belief(state: dict, entity: Entity, payload: dict) -> None:
    """Record an entity's crux-belief poll; resubmission replaces their own.

    Shared by the free-text and structured paths.  ``belief`` is
    float-coerced; ``None`` (defensive — the validated paths never pass
    it) is preserved so ``apply_poll_beliefs`` can skip it.
    """
    belief = payload.get("belief")
    entry = {
        "entity_id": entity.id,
        "entity_name": entity.name,
        "belief": None if belief is None else float(belief),
        "reasoning": str(payload.get("reasoning") or "").strip(),
    }
    polls = state.setdefault("poll_beliefs", [])
    for i, existing in enumerate(polls):
        if existing["entity_id"] == entity.id:
            polls[i] = entry
            return
    polls.append(entry)


def entities_with_poll(state: dict) -> set[int]:
    """Entity ids that have a recorded crux-belief poll."""
    return {e["entity_id"] for e in state.get("poll_beliefs", [])}


def extract_poll_belief(content: str) -> dict | None:
    """Parse a crux-belief poll from free text (fallback path).

    Only a fenced JSON block with a ``belief`` key is accepted.
    """
    data = extract_json_block(content)
    if isinstance(data, dict) and "belief" in data:
        return data
    return None


def apply_poll_beliefs(state: dict) -> None:
    """Replace ``shared_crux['initial_beliefs']`` with the polled values.

    The poll is the authoritative source of the belief-shift metric's
    "before" end (design 2026-07-17): a name->belief map built purely
    from ``poll_beliefs``, dropping any ``None`` belief.  Called once
    when the poll phase completes.
    """
    beliefs = {e["entity_name"]: e["belief"]
               for e in state.get("poll_beliefs", [])
               if e.get("belief") is not None}
    state.setdefault("shared_crux", {})["initial_beliefs"] = beliefs


def redact_belief_lines(content: str) -> str:
    """Drop any ``BELIEF_LINE_PREFIX`` line from a context message.

    The belief poll must be a clean "before" baseline: a later poller
    must not anchor on an earlier poller's stated probability.  Poll
    turns render that probability on its own ``Belief on the crux: <n>``
    line, so removing those lines strips the numeric anchor while leaving
    the surrounding reasoning intact.  Pure — returns a new string; the
    full turn is unaffected in the visible transcript.
    """
    kept = [line for line in content.splitlines()
            if not line.lstrip().startswith(BELIEF_LINE_PREFIX)]
    return "\n".join(kept)
