"""Shared helpers for Nominal Group Technique phase handlers (issue #24).

Pure functions and constants for idea recording/deduplication,
candidate management after clustering, point-allocation validation,
tallying, and display formatting — used by the generate, cluster,
clarify, allocate, and rank phase handlers.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from ..parsing import cluster_text_contributions, extract_json_block

if TYPE_CHECKING:
    from ...models import Entity

logger = logging.getLogger(__name__)

#: Points each participant distributes across candidate ideas.
POINTS_PER_VOTER = 10
#: Minimum character length for an idea / candidate title to be substantive.
MIN_IDEA_LENGTH = 10
#: Word-overlap ratio above which two ideas are considered duplicates.
SIMILARITY_THRESHOLD = 0.7
#: Give up and advance after this many generation rounds without ideas.
MAX_GENERATE_ROUNDS = 3
#: Give up on moderator clustering after this many unparseable responses.
MAX_CLUSTER_ATTEMPTS = 3
#: Give up and advance after this many allocation rounds.
MAX_ALLOCATE_ROUNDS = 3

#: JSON Schema for the submit_ideas output tool (issue #23 pattern).
IDEAS_TOOL_PARAMETERS: dict = {
    "type": "object",
    "properties": {
        "ideas": {
            "type": "array",
            "minItems": 1,
            "items": {"type": "string"},
            "description": ("Your candidate ideas or solutions — each a "
                            "complete, specific, self-contained proposal.  "
                            "Aim for 3-7 distinct ideas; include "
                            "unconventional ones."),
        },
        "reasoning": {
            "type": "string",
            "description": ("Brief rationale: the angle or need each "
                            "idea addresses."),
        },
    },
    "required": ["ideas", "reasoning"],
}

#: JSON Schema for the submit_candidates output tool (moderator clustering).
CANDIDATES_TOOL_PARAMETERS: dict = {
    "type": "object",
    "properties": {
        "candidates": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": ("The consolidated idea as one "
                                        "complete, specific statement."),
                    },
                    "summary": {
                        "type": "string",
                        "description": ("Optional: which raw ideas were "
                                        "merged and any nuance preserved."),
                    },
                },
                "required": ["title"],
            },
        },
        "reasoning": {
            "type": "string",
            "description": ("How you deduplicated and clustered the "
                            "raw ideas."),
        },
    },
    "required": ["candidates", "reasoning"],
}

#: JSON Schema for the submit_points output tool (multi-voting).
ALLOCATIONS_TOOL_PARAMETERS: dict = {
    "type": "object",
    "properties": {
        "allocations": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "properties": {
                    "candidate_id": {"type": "integer"},
                    "points": {"type": "integer", "minimum": 1},
                    "rationale": {"type": "string"},
                },
                "required": ["candidate_id", "points"],
            },
            "description": ("One entry per candidate you give points to; "
                            "points must sum to your full pool."),
        },
        "reasoning": {
            "type": "string",
            "description": "Your overall prioritisation rationale.",
        },
    },
    "required": ["allocations", "reasoning"],
}


def validate_ideas_payload(payload: dict) -> str:
    """Return '' if a submit_ideas payload is usable, else an error."""
    ideas = payload.get("ideas")
    if not isinstance(ideas, list) or not ideas:
        return "'ideas' must be a non-empty array of idea strings."
    for idea in ideas:
        if not isinstance(idea, str) or len(idea.strip()) < MIN_IDEA_LENGTH:
            return ("Each idea must be a complete, specific proposal of "
                    f"at least {MIN_IDEA_LENGTH} characters (got: {idea!r}).")
    if not str(payload.get("reasoning") or "").strip():
        return "'reasoning' must contain your rationale for these ideas."
    return ""


def record_ideas(state: dict, entity: Entity,
                 texts: list[str]) -> list[dict]:
    """Append this turn's ideas as raw contributions, rebuild the
    order-independent clustered view, and return the clusters this
    turn's ideas landed in.

    Every submission is retained in ``state["ideas_raw"]``; the merged
    view ``state["ideas"]`` is derived by clustering the whole raw set
    and labelling each cluster with its medoid, so grouping and label
    are independent of submission order (issue #42).  The clustering
    phase still merges whatever survives this coarse gate.  Shared by
    the free-text and structured-output paths (issue #23).
    """
    raw = state.setdefault("ideas_raw", [])
    since = len(raw)
    for text in texts:
        cleaned = str(text).strip().rstrip('.')
        if len(cleaned) < MIN_IDEA_LENGTH:
            continue
        raw.append({"entity_id": entity.id, "entity_name": entity.name,
                    "text": cleaned})
    view, touched = cluster_text_contributions(
        raw, since=since, threshold=SIMILARITY_THRESHOLD)
    state["ideas"] = view
    return touched


def validate_candidates_payload(payload: dict) -> str:
    """Return '' if a submit_candidates payload is usable, else an error."""
    candidates = payload.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        return "'candidates' must be a non-empty array of candidate objects."
    for c in candidates:
        if not isinstance(c, dict):
            return "Each entry in 'candidates' must be an object."
        title = c.get("title")
        if not isinstance(title, str) or len(title.strip()) < MIN_IDEA_LENGTH:
            return ("Each candidate 'title' must be one complete, specific "
                    f"statement of at least {MIN_IDEA_LENGTH} characters "
                    f"(got: {title!r}).")
        summary = c.get("summary")
        if summary is not None and not isinstance(summary, str):
            return "Each candidate 'summary' must be a string when present."
    if not str(payload.get("reasoning") or "").strip():
        return "'reasoning' must explain how you consolidated the ideas."
    return ""


def record_candidates(state: dict, items: list[dict]) -> None:
    """Replace the candidate list with sequentially-id'd entries.

    Clustering is a single consolidation step, not accumulative — a
    retry replaces the previous (empty) result rather than appending.
    """
    state["candidates"] = [
        {
            "id": i,
            "title": str(item.get("title") or "").strip().rstrip('.'),
            "summary": str(item.get("summary") or "").strip(),
        }
        for i, item in enumerate(items, 1)
    ]


def fallback_candidates_from_ideas(state: dict) -> None:
    """Promote raw deduplicated ideas to candidates 1:1.

    Used when the moderator could not produce a parseable clustering
    after MAX_CLUSTER_ATTEMPTS — voting on the raw ideas is far better
    than ending the method.
    """
    record_candidates(
        state,
        [{"title": idea["text"], "summary": ""}
         for idea in state.get("ideas", [])],
    )


def _validated_allocation_entries(
        allocations: object, valid_ids: set[int], points_pool: int, *,
        coerce_types: bool) -> tuple[list[dict], str]:
    """Check allocation entries against the point-pool rules.

    Shared by the structured validator and the free-text batch check:
    every candidate must exist and appear at most once, every points
    value must be a positive integer, and the points must sum to
    exactly ``points_pool``.  With ``coerce_types=True`` (the free-text
    path) numeric strings are int()-coerced; booleans are rejected on
    both paths — bool is an int subtype, so ``True`` would otherwise
    silently count as candidate 1 or as a single point.

    Returns ``(normalised_entries, "")`` when valid, else
    ``([], error)`` with a human-readable reason.
    """
    if not isinstance(allocations, list) or not allocations:
        return [], "'allocations' must be a non-empty array."
    normalised: list[dict] = []
    seen: set[int] = set()
    total = 0
    for a in allocations:
        if not isinstance(a, dict):
            return [], "Each entry in 'allocations' must be an object."
        raw_id = a.get("candidate_id")
        raw_points = a.get("points")
        if isinstance(raw_id, bool) or isinstance(raw_points, bool):
            return [], ("'candidate_id' and 'points' must be integers, "
                        "not booleans.")
        try:
            candidate_id = int(raw_id)
        except (TypeError, ValueError):
            return [], "Each allocation needs an integer 'candidate_id'."
        if coerce_types:
            try:
                points = int(raw_points)
            except (TypeError, ValueError):
                return [], "Each 'points' value must be a positive integer."
        else:
            if not isinstance(raw_points, int):
                return [], "Each 'points' value must be a positive integer."
            points = raw_points
        if candidate_id not in valid_ids:
            return [], (f"Candidate {candidate_id} does not exist. Valid "
                        f"candidate ids: {sorted(valid_ids)}.")
        if candidate_id in seen:
            return [], (f"Candidate {candidate_id} appears more than once — "
                        "submit at most one entry per candidate.")
        seen.add(candidate_id)
        if points < 1:
            return [], "Each 'points' value must be a positive integer."
        total += points
        normalised.append({
            "candidate_id": candidate_id,
            "points": points,
            "rationale": str(a.get("rationale") or ""),
        })
    if total != points_pool:
        return [], (f"Your points must sum to exactly {points_pool} "
                    f"(you allocated {total}).")
    return normalised, ""


def validate_allocations_payload(payload: dict, valid_ids: set[int],
                                 points_pool: int) -> str:
    """Return '' if a submit_points payload is usable, else an error."""
    _, error = _validated_allocation_entries(
        payload.get("allocations"), valid_ids, points_pool,
        coerce_types=False)
    if error:
        return error
    if not str(payload.get("reasoning") or "").strip():
        return "'reasoning' must contain your prioritisation rationale."
    return ""


def check_free_text_allocations(
        state: dict, entity: Entity,
        allocations: list[dict]) -> tuple[list[dict], str]:
    """Enforce the point-pool rules on a free-text allocation batch.

    The structured path enforces these rules in
    ``validate_allocations_payload``; without the same gate here a
    free-text turn (a human participant, or an AI that exhausted its
    structured retries) could allocate an arbitrary total, or top up
    further candidates on a later turn — silently multiplying that
    participant's voting power.  Numeric strings are coerced (humans
    and JSON blocks may quote numbers); the batch is all-or-nothing.

    Returns ``(normalised_allocations, "")`` when the batch may be
    recorded, else ``([], error)`` with a user-facing reason.
    """
    if entity.id in entities_with_allocations(state):
        return [], ("You have already allocated your points — additional "
                    "allocations are not counted.")
    valid_ids = {c["id"] for c in state.get("candidates", [])}
    pool = state.get("points_per_voter", POINTS_PER_VOTER)
    return _validated_allocation_entries(
        allocations, valid_ids, pool, coerce_types=True)


def record_allocations(state: dict, entity: Entity,
                       allocations: list[dict]) -> int:
    """Validate ids, dedupe, and append allocations; return count accepted.

    Shared by the free-text and structured-output paths (issue #23).
    Skips allocations for unknown candidates and for candidates this
    entity has already allocated points to.
    """
    valid_ids = {c["id"] for c in state.get("candidates", [])}
    recorded = state.setdefault("point_allocations", [])
    accepted = 0
    for a in allocations:
        try:
            candidate_id = int(a.get("candidate_id"))
        except (TypeError, ValueError):
            logger.warning(
                "Allocation with non-numeric candidate_id %r from %s, "
                "skipping", a.get("candidate_id"), entity.name)
            continue
        if candidate_id not in valid_ids:
            logger.warning(
                "Allocation for unknown candidate %s from %s, skipping",
                candidate_id, entity.name)
            continue
        try:
            points = int(a.get("points"))
        except (TypeError, ValueError):
            logger.warning(
                "Allocation with non-numeric points %r from %s, skipping",
                a.get("points"), entity.name)
            continue
        if points < 1:
            logger.warning(
                "Allocation with non-positive points %d from %s, skipping",
                points, entity.name)
            continue
        if any(r["entity_id"] == entity.id
               and r["candidate_id"] == candidate_id for r in recorded):
            logger.info(
                "%s already allocated points to candidate %d, skipping "
                "duplicate", entity.name, candidate_id)
            continue
        recorded.append({
            "entity_id": entity.id,
            "entity_name": entity.name,
            "candidate_id": candidate_id,
            "points": points,
            "rationale": str(a.get("rationale") or ""),
        })
        accepted += 1
    return accepted


def extract_allocations(content: str) -> list[dict]:
    """Parse point allocations from free text (human/fallback path).

    Tries a fenced JSON block with an ``allocations`` array first,
    then per-line ``Candidate 3: 4 points`` patterns.
    """
    data = extract_json_block(content)
    if isinstance(data, dict) and isinstance(data.get("allocations"), list):
        return [a for a in data["allocations"] if isinstance(a, dict)]
    allocations: list[dict] = []
    for match in re.finditer(
            r'candidate\s*#?(\d+)\s*[:\-–—]\s*(\d+)\s*points?',
            content, re.IGNORECASE):
        allocations.append({"candidate_id": int(match.group(1)),
                            "points": int(match.group(2)),
                            "rationale": ""})
    return allocations


def entities_with_allocations(state: dict) -> set[int]:
    """Entity ids that have at least one recorded point allocation."""
    return {r["entity_id"] for r in state.get("point_allocations", [])}


def tally_points(state: dict) -> dict[int, int]:
    """Total points per candidate id (candidates with no points → 0)."""
    totals = {c["id"]: 0 for c in state.get("candidates", [])}
    for r in state.get("point_allocations", []):
        if r["candidate_id"] in totals:
            totals[r["candidate_id"]] += r["points"]
    return totals


def format_ideas_for_clustering(state: dict) -> str:
    """Numbered raw-idea list shown to the clustering moderator."""
    ideas = state.get("ideas", [])
    if not ideas:
        return "  (No ideas were recorded)"
    return "\n".join(f"  Idea {i['id']}: {i['text']}" for i in ideas)


def format_candidates(state: dict) -> str:
    """Candidate list with ids for the clarify and voting phases."""
    candidates = state.get("candidates", [])
    if not candidates:
        return "  (No candidates)"
    lines = []
    for c in candidates:
        line = f"  Candidate {c['id']}: {c['title']}"
        if c.get("summary"):
            line += f" — {c['summary']}"
        lines.append(line)
    return "\n".join(lines)


def format_ranked_candidates(state: dict) -> str:
    """Candidates ranked by total points, with participant counts."""
    candidates = state.get("candidates", [])
    if not candidates:
        return "  (No candidates)"
    totals = tally_points(state)
    voters = {c["id"]: 0 for c in candidates}
    for r in state.get("point_allocations", []):
        if r["candidate_id"] in voters:
            voters[r["candidate_id"]] += 1
    ranked = sorted(candidates, key=lambda c: totals[c["id"]], reverse=True)
    return "\n".join(
        f"  {rank}. {c['title']} — {totals[c['id']]} point(s) "
        f"from {voters[c['id']]} participant(s)"
        for rank, c in enumerate(ranked, 1)
    )
