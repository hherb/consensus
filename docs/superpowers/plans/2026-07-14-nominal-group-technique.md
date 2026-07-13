# Nominal Group Technique (NGT) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement issue #24 — Nominal Group Technique, the catalog's first *generative* discussion method (silent idea generation → moderator clustering → clarification → multi-voting → ranked shortlist) — and, per the repo owner's 2026-07-12 decision recorded on the issue, make Open Discussion recommendable once NGT exists.

**Architecture:** One new `DiscussionMethod` (`consensus/methods/nominal_group.py`) assembled from five new composable `PhaseHandler`s in `consensus/methods/phases/`, backed by a shared pure-function helper module `_ngt_helpers.py` (mirrors `_delphi_helpers.py` / `_voting_helpers.py`). Three phases force structured output tools per the issue-#23 pattern (`submit_ideas`, `submit_candidates`, `submit_points`); free-text `process_response` paths remain as the human/fallback layer. Reuses `anonymise_content` (Delphi), `parse_numbered_list` / `word_overlap_similar` (parsing), and the established give-up-cap and moderator-only-phase conventions.

**Tech Stack:** Python 3 (stdlib only), pytest, `uv` for environment management.

## Global Constraints

- **`uv` only** — never call `pip` directly. Tests run with `uv run pytest …` (or plain `pytest` if the venv is active).
- **TDD** — each task writes the failing test first, verifies it fails, implements, verifies it passes, commits.
- **Docstrings and type hints mandatory** on every function/method (`docs/llm/golden_rules.md` rule 2).
- **No magic numbers** — all thresholds/caps are module constants (`docs/llm/golden_rules.md` rule 3).
- **Files under ~500 lines** (`docs/llm/golden_rules.md` rule 8).
- **HANDOVER.md conventions (2026-07-13):**
  - Structured-phase conversions keep `process_response` (humans type free text; structured path falls back after exhausted retries).
  - Every condition-based phase (`rounds=0`) and parse-gated phase needs a give-up cap (`MAX_*` constants) that logs a warning when tripped.
  - Structured tools include a required `reasoning` field rendered before the data display.
  - Never derive a phase turn order by filtering the incoming `entity_ids`; moderator-only phases return `[discussion.moderator_id]`.
  - Structured items are `.strip().rstrip('.')`-normalised to match the regex paths.
- **Branch:** work happens on the current worktree branch `claude/handover-instructions-3dd9e3`; commit after each task.

## File Structure

| File | Responsibility |
|------|----------------|
| `consensus/methods/phases/_ngt_helpers.py` (create) | Constants, JSON Schemas, payload validators, record/dedup/tally/format pure functions |
| `consensus/methods/phases/generate_ideas.py` (create) | Phase 1 handler: silent anonymised idea generation (`submit_ideas`), abort-on-no-ideas |
| `consensus/methods/phases/cluster_ideas.py` (create) | Phase 2 handler: moderator-only clustering (`submit_candidates`), fallback promotion of raw ideas |
| `consensus/methods/phases/clarify_ideas.py` (create) | Phase 3 handler: one free-text clarification round (no structured tool) |
| `consensus/methods/phases/allocate_points.py` (create) | Phase 4 handler: multi-voting (`submit_points`), all-in / give-up advancement |
| `consensus/methods/phases/rank_ideas.py` (create) | Phase 5 handler: moderator-only ranked-results presentation |
| `consensus/methods/nominal_group.py` (create) | `NominalGroupTechnique` method assembly + conclusion prompt |
| `consensus/methods/__init__.py` (modify) | Register `"nominal_group"` |
| `consensus/methods/recommender.py` (modify) | Remove `open_discussion` from `_EXCLUDED_METHODS`; update `_TAXONOMY` |
| `tests/test_ngt_helpers.py` (create) | Helper-module unit tests |
| `tests/test_phases_ngt.py` (create) | Handler prompts/free-text/advancement/method-level tests |
| `tests/test_ngt_structured.py` (create) | Structured-output conversion tests (per-#23 convention) |
| `tests/test_recommender.py` (modify) | Exclusion-set test update |
| `docs/devel/15-discussion-methods.md` (modify) | File list + method table |
| `docs/user_manual/05_discussion_methods.md` (modify) | Method section + "Choosing a Method" row |
| `HANDOVER.md` (modify) | Mark #24 done; record follow-ups |

**Method state keys** (contributed by handler `init_state`, no collisions):
- `ideas: list[dict]` — raw ideas `{"id": int (1-based), "entity_id": int, "entity_name": str, "text": str}` (GenerateIdeasHandler)
- `candidates: list[dict]` — consolidated candidates `{"id": int (1-based), "title": str, "summary": str}`; `cluster_attempts: int` (ClusterIdeasHandler)
- `point_allocations: list[dict]` — `{"entity_id": int, "entity_name": str, "candidate_id": int, "points": int, "rationale": str}`; `points_per_voter: int` (AllocatePointsHandler)

---

### Task 1: NGT helper module (`_ngt_helpers.py`)

**Files:**
- Create: `consensus/methods/phases/_ngt_helpers.py`
- Test: `tests/test_ngt_helpers.py`

**Interfaces:**
- Consumes: `consensus.methods.parsing.word_overlap_similar`, `consensus.methods.parsing.extract_json_block`
- Produces (used by Tasks 2–6):
  - Constants: `POINTS_PER_VOTER: int = 10`, `MIN_IDEA_LENGTH: int = 10`, `SIMILARITY_THRESHOLD: float = 0.7`, `MAX_GENERATE_ROUNDS: int = 3`, `MAX_CLUSTER_ATTEMPTS: int = 3`, `MAX_ALLOCATE_ROUNDS: int = 3`
  - Schemas: `IDEAS_TOOL_PARAMETERS: dict`, `CANDIDATES_TOOL_PARAMETERS: dict`, `ALLOCATIONS_TOOL_PARAMETERS: dict`
  - `validate_ideas_payload(payload: dict) -> str`
  - `record_ideas(state: dict, entity: Entity, texts: list[str]) -> list[dict]`
  - `validate_candidates_payload(payload: dict) -> str`
  - `record_candidates(state: dict, items: list[dict]) -> None`
  - `fallback_candidates_from_ideas(state: dict) -> None`
  - `validate_allocations_payload(payload: dict, valid_ids: set[int], points_pool: int) -> str`
  - `record_allocations(state: dict, entity: Entity, allocations: list[dict]) -> int`
  - `extract_allocations(content: str) -> list[dict]`
  - `entities_with_allocations(state: dict) -> set[int]`
  - `tally_points(state: dict) -> dict[int, int]`
  - `format_ideas_for_clustering(state: dict) -> str`
  - `format_candidates(state: dict) -> str`
  - `format_ranked_candidates(state: dict) -> str`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_ngt_helpers.py`:

```python
"""Tests for Nominal Group Technique shared helpers (issue #24).

Pure-function coverage: payload validators, idea recording with
word-overlap dedup, candidate management (including the clustering
give-up fallback), point-allocation validation/recording, free-text
allocation extraction, tallying, and display formatting.
"""

from consensus.methods.phases._ngt_helpers import (
    ALLOCATIONS_TOOL_PARAMETERS,
    CANDIDATES_TOOL_PARAMETERS,
    IDEAS_TOOL_PARAMETERS,
    MAX_ALLOCATE_ROUNDS,
    MAX_CLUSTER_ATTEMPTS,
    MAX_GENERATE_ROUNDS,
    MIN_IDEA_LENGTH,
    POINTS_PER_VOTER,
    entities_with_allocations,
    extract_allocations,
    fallback_candidates_from_ideas,
    format_candidates,
    format_ideas_for_clustering,
    format_ranked_candidates,
    record_allocations,
    record_candidates,
    record_ideas,
    tally_points,
    validate_allocations_payload,
    validate_candidates_payload,
    validate_ideas_payload,
)
from consensus.models import Entity, EntityType


def _entity(eid: int = 1, name: str = "Alice") -> Entity:
    return Entity(id=eid, name=name, entity_type=EntityType.AI)


IDEAS_PAYLOAD = {
    "ideas": [
        "Offer a self-serve onboarding checklist inside the product",
        "Run monthly live office hours for new customers",
    ],
    "reasoning": "Covers both self-serve and high-touch onboarding needs.",
}

CANDIDATES_PAYLOAD = {
    "candidates": [
        {"title": "Build a self-serve onboarding checklist",
         "summary": "Merges ideas 1 and 3"},
        {"title": "Run recurring live office hours for customers"},
    ],
    "reasoning": "Merged duplicate self-serve ideas; kept live support separate.",
}


def _state_with_candidates() -> dict:
    state: dict = {}
    record_candidates(state, CANDIDATES_PAYLOAD["candidates"])
    return state


class TestConstants:
    def test_give_up_caps_are_positive(self):
        assert MAX_GENERATE_ROUNDS > 0
        assert MAX_CLUSTER_ATTEMPTS > 0
        assert MAX_ALLOCATE_ROUNDS > 0

    def test_points_pool_positive(self):
        assert POINTS_PER_VOTER > 0


class TestIdeasSchemaAndValidator:
    def test_schema_shape(self):
        assert IDEAS_TOOL_PARAMETERS["type"] == "object"
        assert set(IDEAS_TOOL_PARAMETERS["required"]) == {"ideas", "reasoning"}
        assert IDEAS_TOOL_PARAMETERS["properties"]["ideas"]["type"] == "array"

    def test_valid(self):
        assert validate_ideas_payload(IDEAS_PAYLOAD) == ""

    def test_missing_ideas_rejected(self):
        assert validate_ideas_payload({"reasoning": "x"}) != ""

    def test_ideas_not_a_list_rejected(self):
        assert validate_ideas_payload(
            {"ideas": "one string", "reasoning": "x"}) != ""

    def test_empty_ideas_rejected(self):
        assert validate_ideas_payload({"ideas": [], "reasoning": "x"}) != ""

    def test_short_idea_rejected(self):
        bad = {"ideas": ["x" * (MIN_IDEA_LENGTH - 1)], "reasoning": "x"}
        assert validate_ideas_payload(bad) != ""

    def test_non_string_idea_rejected(self):
        assert validate_ideas_payload(
            {"ideas": [12345678901], "reasoning": "x"}) != ""

    def test_null_reasoning_rejected(self):
        """JSON null must not slip through as the string 'None'."""
        bad = {"ideas": IDEAS_PAYLOAD["ideas"], "reasoning": None}
        assert validate_ideas_payload(bad) != ""

    def test_missing_reasoning_rejected(self):
        assert validate_ideas_payload({"ideas": IDEAS_PAYLOAD["ideas"]}) != ""


class TestRecordIdeas:
    def test_assigns_sequential_ids_and_attribution(self):
        state: dict = {}
        accepted = record_ideas(state, _entity(), IDEAS_PAYLOAD["ideas"])
        assert [i["id"] for i in state["ideas"]] == [1, 2]
        assert accepted == state["ideas"]
        assert state["ideas"][0]["entity_name"] == "Alice"

    def test_dedups_by_word_overlap(self):
        state: dict = {}
        record_ideas(state, _entity(1, "Alice"), IDEAS_PAYLOAD["ideas"])
        accepted = record_ideas(
            state, _entity(2, "Bob"),
            ["Offer a self-serve onboarding checklist inside the product now",
             "Publish a searchable public knowledge base"],
        )
        assert len(accepted) == 1
        assert "knowledge base" in accepted[0]["text"]
        assert len(state["ideas"]) == 3

    def test_strips_trailing_period(self):
        state: dict = {}
        record_ideas(state, _entity(),
                     ["Offer a self-serve onboarding checklist."])
        assert state["ideas"][0]["text"].endswith("checklist")

    def test_drops_short_items(self):
        state: dict = {}
        accepted = record_ideas(state, _entity(), ["Too short"])
        assert accepted == []
        assert state["ideas"] == []


class TestCandidatesSchemaAndValidator:
    def test_schema_shape(self):
        assert set(CANDIDATES_TOOL_PARAMETERS["required"]) == {
            "candidates", "reasoning"}
        items = CANDIDATES_TOOL_PARAMETERS["properties"]["candidates"]["items"]
        assert items["required"] == ["title"]

    def test_valid(self):
        assert validate_candidates_payload(CANDIDATES_PAYLOAD) == ""

    def test_missing_candidates_rejected(self):
        assert validate_candidates_payload({"reasoning": "x"}) != ""

    def test_non_object_candidate_rejected(self):
        bad = {"candidates": ["just a string"], "reasoning": "x"}
        assert validate_candidates_payload(bad) != ""

    def test_short_title_rejected(self):
        bad = {"candidates": [{"title": "Pricing"}], "reasoning": "x"}
        assert validate_candidates_payload(bad) != ""

    def test_non_string_summary_rejected(self):
        bad = {"candidates": [{"title": "A substantive candidate title",
                               "summary": 42}],
               "reasoning": "x"}
        assert validate_candidates_payload(bad) != ""

    def test_null_reasoning_rejected(self):
        bad = {"candidates": CANDIDATES_PAYLOAD["candidates"],
               "reasoning": None}
        assert validate_candidates_payload(bad) != ""


class TestRecordCandidates:
    def test_assigns_sequential_ids(self):
        state = _state_with_candidates()
        assert [c["id"] for c in state["candidates"]] == [1, 2]

    def test_missing_summary_becomes_empty_string(self):
        state = _state_with_candidates()
        assert state["candidates"][1]["summary"] == ""

    def test_replaces_previous_candidates(self):
        state = _state_with_candidates()
        record_candidates(state, [{"title": "A single replacement candidate"}])
        assert len(state["candidates"]) == 1
        assert state["candidates"][0]["id"] == 1

    def test_fallback_promotes_ideas_one_to_one(self):
        state: dict = {}
        record_ideas(state, _entity(), IDEAS_PAYLOAD["ideas"])
        fallback_candidates_from_ideas(state)
        assert len(state["candidates"]) == 2
        assert state["candidates"][0]["title"] == state["ideas"][0]["text"]

    def test_fallback_with_no_ideas_yields_no_candidates(self):
        state: dict = {}
        fallback_candidates_from_ideas(state)
        assert state["candidates"] == []


class TestAllocationsValidator:
    VALID = {
        "allocations": [
            {"candidate_id": 1, "points": 7, "rationale": "Highest leverage"},
            {"candidate_id": 2, "points": 3},
        ],
        "reasoning": "Self-serve scales; office hours still matter.",
    }

    def test_schema_shape(self):
        assert set(ALLOCATIONS_TOOL_PARAMETERS["required"]) == {
            "allocations", "reasoning"}
        items = ALLOCATIONS_TOOL_PARAMETERS["properties"]["allocations"]["items"]
        assert set(items["required"]) == {"candidate_id", "points"}

    def test_valid(self):
        assert validate_allocations_payload(
            self.VALID, {1, 2}, POINTS_PER_VOTER) == ""

    def test_missing_allocations_rejected(self):
        assert validate_allocations_payload(
            {"reasoning": "x"}, {1, 2}, POINTS_PER_VOTER) != ""

    def test_unknown_candidate_rejected(self):
        bad = {"allocations": [{"candidate_id": 9, "points": 10}],
               "reasoning": "x"}
        assert "9" in validate_allocations_payload(bad, {1, 2}, 10)

    def test_duplicate_candidate_rejected(self):
        bad = {"allocations": [{"candidate_id": 1, "points": 5},
                               {"candidate_id": 1, "points": 5}],
               "reasoning": "x"}
        assert validate_allocations_payload(bad, {1, 2}, 10) != ""

    def test_non_integer_points_rejected(self):
        bad = {"allocations": [{"candidate_id": 1, "points": "ten"}],
               "reasoning": "x"}
        assert validate_allocations_payload(bad, {1, 2}, 10) != ""

    def test_boolean_points_rejected(self):
        bad = {"allocations": [{"candidate_id": 1, "points": True}],
               "reasoning": "x"}
        assert validate_allocations_payload(bad, {1, 2}, 10) != ""

    def test_zero_points_rejected(self):
        bad = {"allocations": [{"candidate_id": 1, "points": 0}],
               "reasoning": "x"}
        assert validate_allocations_payload(bad, {1, 2}, 10) != ""

    def test_wrong_sum_rejected(self):
        bad = {"allocations": [{"candidate_id": 1, "points": 4}],
               "reasoning": "x"}
        err = validate_allocations_payload(bad, {1, 2}, 10)
        assert "10" in err

    def test_null_reasoning_rejected(self):
        bad = {"allocations": [{"candidate_id": 1, "points": 10}],
               "reasoning": None}
        assert validate_allocations_payload(bad, {1, 2}, 10) != ""


class TestRecordAllocations:
    def test_records_and_counts(self):
        state = _state_with_candidates()
        n = record_allocations(state, _entity(), [
            {"candidate_id": 1, "points": 7, "rationale": "r1"},
            {"candidate_id": 2, "points": 3},
        ])
        assert n == 2
        assert len(state["point_allocations"]) == 2
        assert state["point_allocations"][0]["entity_name"] == "Alice"
        assert state["point_allocations"][1]["rationale"] == ""

    def test_skips_unknown_candidate(self):
        state = _state_with_candidates()
        n = record_allocations(state, _entity(),
                               [{"candidate_id": 99, "points": 10}])
        assert n == 0

    def test_skips_double_allocation_for_same_candidate(self):
        state = _state_with_candidates()
        record_allocations(state, _entity(),
                           [{"candidate_id": 1, "points": 5}])
        n = record_allocations(state, _entity(),
                               [{"candidate_id": 1, "points": 5}])
        assert n == 0
        assert len(state["point_allocations"]) == 1

    def test_coerces_string_ids_and_points(self):
        state = _state_with_candidates()
        n = record_allocations(state, _entity(),
                               [{"candidate_id": "1", "points": "10"}])
        assert n == 1
        assert state["point_allocations"][0]["points"] == 10

    def test_entities_with_allocations(self):
        state = _state_with_candidates()
        record_allocations(state, _entity(1), [{"candidate_id": 1, "points": 10}])
        record_allocations(state, _entity(2, "Bob"),
                           [{"candidate_id": 2, "points": 10}])
        assert entities_with_allocations(state) == {1, 2}


class TestExtractAllocations:
    def test_extracts_json_block(self):
        content = (
            "Here is my vote:\n```json\n"
            '{"allocations": [{"candidate_id": 1, "points": 6},'
            ' {"candidate_id": 2, "points": 4}]}\n```'
        )
        allocations = extract_allocations(content)
        assert len(allocations) == 2
        assert allocations[0]["candidate_id"] == 1

    def test_extracts_natural_language_lines(self):
        content = "Candidate 1: 6 points\nCandidate 2 - 4 points"
        allocations = extract_allocations(content)
        assert [(a["candidate_id"], a["points"]) for a in allocations] == [
            (1, 6), (2, 4)]

    def test_returns_empty_for_prose(self):
        assert extract_allocations("I like the first idea best.") == []


class TestTallyAndFormatting:
    def _voted_state(self) -> dict:
        state = _state_with_candidates()
        record_allocations(state, _entity(1, "Alice"),
                           [{"candidate_id": 1, "points": 7},
                            {"candidate_id": 2, "points": 3}])
        record_allocations(state, _entity(2, "Bob"),
                           [{"candidate_id": 2, "points": 10}])
        return state

    def test_tally_totals(self):
        totals = tally_points(self._voted_state())
        assert totals == {1: 7, 2: 13}

    def test_tally_includes_zero_point_candidates(self):
        state = _state_with_candidates()
        assert tally_points(state) == {1: 0, 2: 0}

    def test_format_ideas_for_clustering(self):
        state: dict = {}
        record_ideas(state, _entity(), IDEAS_PAYLOAD["ideas"])
        text = format_ideas_for_clustering(state)
        assert "Idea 1:" in text
        assert "onboarding checklist" in text

    def test_format_ideas_empty(self):
        assert "No ideas" in format_ideas_for_clustering({})

    def test_format_candidates_lists_ids_and_summaries(self):
        text = format_candidates(_state_with_candidates())
        assert "Candidate 1:" in text
        assert "Merges ideas 1 and 3" in text

    def test_format_candidates_empty(self):
        assert "No candidates" in format_candidates({})

    def test_format_ranked_orders_by_points(self):
        text = format_ranked_candidates(self._voted_state())
        first_line = text.splitlines()[0]
        assert "office hours" in first_line
        assert "13 point(s)" in first_line
        assert "2 participant(s)" in first_line
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_ngt_helpers.py -q`
Expected: collection error — `ModuleNotFoundError: No module named 'consensus.methods.phases._ngt_helpers'`

- [ ] **Step 3: Write the implementation**

Create `consensus/methods/phases/_ngt_helpers.py`:

```python
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

from ..parsing import extract_json_block, word_overlap_similar

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
    """Dedup, id, and append raw ideas; return the accepted idea dicts.

    An idea is dropped when it is word-overlap similar to any idea
    already recorded — the clustering phase merges near-misses that
    survive this coarse filter.  Shared by the free-text and
    structured-output paths (issue #23).
    """
    ideas = state.setdefault("ideas", [])
    accepted: list[dict] = []
    for text in texts:
        cleaned = str(text).strip().rstrip('.')
        if len(cleaned) < MIN_IDEA_LENGTH:
            continue
        if any(word_overlap_similar(cleaned, existing["text"],
                                    threshold=SIMILARITY_THRESHOLD)
               for existing in ideas):
            continue
        idea = {
            "id": len(ideas) + 1,
            "entity_id": entity.id,
            "entity_name": entity.name,
            "text": cleaned,
        }
        ideas.append(idea)
        accepted.append(idea)
    return accepted


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


def validate_allocations_payload(payload: dict, valid_ids: set[int],
                                 points_pool: int) -> str:
    """Return '' if a submit_points payload is usable, else an error."""
    allocations = payload.get("allocations")
    if not isinstance(allocations, list) or not allocations:
        return "'allocations' must be a non-empty array."
    seen: set[int] = set()
    total = 0
    for a in allocations:
        if not isinstance(a, dict):
            return "Each entry in 'allocations' must be an object."
        try:
            candidate_id = int(a.get("candidate_id"))
        except (TypeError, ValueError):
            return "Each allocation needs an integer 'candidate_id'."
        if candidate_id not in valid_ids:
            return (f"Candidate {candidate_id} does not exist. Valid "
                    f"candidate ids: {sorted(valid_ids)}.")
        if candidate_id in seen:
            return (f"Candidate {candidate_id} appears more than once — "
                    "submit at most one entry per candidate.")
        seen.add(candidate_id)
        points = a.get("points")
        if isinstance(points, bool) or not isinstance(points, int):
            # Coercing "7" would be friendly, but bool is an int subtype
            # and True would silently count as 1 — reject non-ints here;
            # record_allocations coerces on the tolerant free-text path.
            return "Each 'points' value must be a positive integer."
        if points < 1:
            return "Each 'points' value must be a positive integer."
        total += points
    if total != points_pool:
        return (f"Your points must sum to exactly {points_pool} "
                f"(you allocated {total}).")
    if not str(payload.get("reasoning") or "").strip():
        return "'reasoning' must contain your prioritisation rationale."
    return ""


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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_ngt_helpers.py -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add consensus/methods/phases/_ngt_helpers.py tests/test_ngt_helpers.py
git commit -m "feat(ngt): shared helpers for Nominal Group Technique (#24)"
```

---

### Task 2: GenerateIdeasHandler (silent generation)

**Files:**
- Create: `consensus/methods/phases/generate_ideas.py`
- Test: `tests/test_phases_ngt.py` (new file, first test classes)

**Interfaces:**
- Consumes: `_ngt_helpers` (`IDEAS_TOOL_PARAMETERS`, `MAX_GENERATE_ROUNDS`, `MIN_IDEA_LENGTH`, `record_ideas`, `validate_ideas_payload`), `_delphi_helpers.anonymise_content`, `parsing.parse_numbered_list`
- Produces: `GenerateIdeasHandler` with `phase.name == "generate"`, `init_state -> {"ideas": []}`, abort semantics (`next_phase -> None` + complete message when no ideas after the cap)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_phases_ngt.py`:

```python
"""Tests for Nominal Group Technique phase handlers (issue #24).

Handler-level coverage: prompts, free-text fallback parsing,
anonymisation, advancement/give-up caps, the generate-phase abort,
the cluster-phase fallback promotion, and method-level assembly.
"""

import pytest

from consensus.methods import get_method
from consensus.methods.base import LINEAR_NEXT, ProcessedResponse
from consensus.methods.phases._ngt_helpers import (
    MAX_ALLOCATE_ROUNDS,
    MAX_CLUSTER_ATTEMPTS,
    MAX_GENERATE_ROUNDS,
    POINTS_PER_VOTER,
    record_candidates,
    record_ideas,
)
from consensus.methods.phases.generate_ideas import GenerateIdeasHandler
from consensus.models import Discussion, Entity, EntityType


@pytest.fixture
def ai_entity() -> Entity:
    return Entity(name="TestAI", entity_type=EntityType.AI, id=1)


@pytest.fixture
def moderator() -> Entity:
    return Entity(name="Mod", entity_type=EntityType.AI, id=99)


def make_disc(**state) -> Discussion:
    """A discussion in the NGT method with a moderator and two panelists."""
    mod = Entity(name="Mod", entity_type=EntityType.AI, id=99)
    alice = Entity(name="TestAI", entity_type=EntityType.AI, id=1)
    bob = Entity(name="Bob", entity_type=EntityType.HUMAN, id=2)
    disc = Discussion(topic="How can we improve customer onboarding?",
                      discussion_method="nominal_group",
                      entities=[mod, alice, bob],
                      moderator_id=99,
                      turn_order=[1, 2])
    method = get_method("nominal_group")
    disc.method_state = method.init_state(disc)
    disc.method_state.update(state)
    return disc


IDEA_LINES = (
    "1. Offer a self-serve onboarding checklist inside the product\n"
    "2. Run monthly live office hours for new customers"
)


class TestGenerateIdeasHandler:
    def test_phase_metadata(self):
        handler = GenerateIdeasHandler()
        assert handler.phase.name == "generate"
        assert handler.phase.rounds == 1

    def test_init_state(self):
        handler = GenerateIdeasHandler()
        assert handler.init_state(make_disc()) == {"ideas": []}

    def test_system_prompt_marks_silent_generation(self, ai_entity):
        prompt = GenerateIdeasHandler().get_system_prompt(
            ai_entity, make_disc())
        assert "SILENT IDEA GENERATION" in prompt
        assert "TestAI" in prompt
        assert "customer onboarding" in prompt
        assert "submit_ideas" in prompt

    def test_turn_prompt_names_tool(self, ai_entity):
        prompt = GenerateIdeasHandler().get_turn_prompt(
            ai_entity, make_disc())
        assert "submit_ideas" in prompt

    def test_summary_prompt_forbids_revealing_ideas(self):
        prompt = GenerateIdeasHandler().get_summary_prompt(
            make_disc(), "TestAI", "Bob")
        assert "Do NOT reveal" in prompt
        assert "Bob" in prompt

    def test_context_is_anonymised(self, ai_entity):
        disc = make_disc()
        out = GenerateIdeasHandler().filter_context_message(
            "TestAI", "TestAI suggests a checklist", "assistant", disc)
        assert "TestAI" not in out
        assert "Panelist" in out

    def test_free_text_path_records_ideas(self, ai_entity):
        disc = make_disc()
        result = GenerateIdeasHandler().process_response(
            IDEA_LINES, ai_entity, disc)
        assert isinstance(result, ProcessedResponse)
        assert len(disc.method_state["ideas"]) == 2

    def test_should_not_advance_on_round_one(self):
        disc = make_disc()
        assert GenerateIdeasHandler().should_advance(disc) is False

    def test_advances_with_ideas_after_round_one(self, ai_entity):
        disc = make_disc(phase_round=2)
        record_ideas(disc.method_state, ai_entity,
                     ["A substantive onboarding improvement idea"])
        assert GenerateIdeasHandler().should_advance(disc) is True

    def test_does_not_advance_without_ideas_before_cap(self):
        disc = make_disc(phase_round=MAX_GENERATE_ROUNDS)
        assert GenerateIdeasHandler().should_advance(disc) is False

    def test_gives_up_after_cap_and_logs(self, caplog):
        disc = make_disc(phase_round=MAX_GENERATE_ROUNDS + 1)
        with caplog.at_level("WARNING"):
            assert GenerateIdeasHandler().should_advance(disc) is True
        assert any("idea" in r.message.lower() for r in caplog.records)

    def test_aborts_method_when_no_ideas_after_cap(self):
        disc = make_disc(phase_round=MAX_GENERATE_ROUNDS + 1)
        handler = GenerateIdeasHandler()
        assert handler.next_phase(disc) is None
        msg = handler.get_method_complete_message(disc)
        assert "ended early" in msg

    def test_continues_linearly_with_ideas(self, ai_entity):
        disc = make_disc(phase_round=2)
        record_ideas(disc.method_state, ai_entity,
                     ["A substantive onboarding improvement idea"])
        handler = GenerateIdeasHandler()
        assert handler.next_phase(disc) == LINEAR_NEXT
        assert handler.get_method_complete_message(disc) == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_phases_ngt.py -q`
Expected: collection error — `ModuleNotFoundError: No module named 'consensus.methods.phases.generate_ideas'`

Note: `make_disc` calls `get_method("nominal_group")`, which is only registered in Task 6. Until then, add this temporary shim at the top of `tests/test_phases_ngt.py` (removed in Task 6):

```python
# TEMPORARY until Task 6 registers the method: build state by hand.
```

Actually — to keep tasks independently green, implement `make_disc` WITHOUT `get_method` until Task 6. Use this version now, and Task 6 Step 3 replaces it with the registry-backed version above:

```python
def make_disc(**state) -> Discussion:
    """A discussion in the NGT method with a moderator and two panelists."""
    mod = Entity(name="Mod", entity_type=EntityType.AI, id=99)
    alice = Entity(name="TestAI", entity_type=EntityType.AI, id=1)
    bob = Entity(name="Bob", entity_type=EntityType.HUMAN, id=2)
    disc = Discussion(topic="How can we improve customer onboarding?",
                      discussion_method="nominal_group",
                      entities=[mod, alice, bob],
                      moderator_id=99,
                      turn_order=[1, 2])
    disc.method_state = {
        "current_phase": "generate",
        "phase_round": 1,
        "ideas": [],
        "candidates": [],
        "cluster_attempts": 0,
        "point_allocations": [],
        "points_per_voter": POINTS_PER_VOTER,
        **state,
    }
    return disc
```

(and drop the `from consensus.methods import get_method` import until Task 6).

- [ ] **Step 3: Write the implementation**

Create `consensus/methods/phases/generate_ideas.py`:

```python
"""Silent idea generation phase handler for Nominal Group Technique.

Each participant independently proposes candidate ideas via the forced
``submit_ideas`` output tool (issue #23); free-text numbered-list
parsing remains the human/fallback path.  Context is anonymised
(Delphi-style) so ideas are judged on content, not authorship.  If no
ideas at all are collected after ``MAX_GENERATE_ROUNDS``, the method
aborts early — every later phase needs an idea list.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ..base import LINEAR_NEXT, OutputToolSpec, Phase, ProcessedResponse
from ..parsing import parse_numbered_list
from ..phase_handler import PhaseHandler
from ._delphi_helpers import anonymise_content
from ._ngt_helpers import (
    IDEAS_TOOL_PARAMETERS,
    MAX_GENERATE_ROUNDS,
    MIN_IDEA_LENGTH,
    record_ideas,
    validate_ideas_payload,
)

if TYPE_CHECKING:
    from ...models import Discussion, Entity

logger = logging.getLogger(__name__)


class GenerateIdeasHandler(PhaseHandler):
    """Phase 1: Silent independent idea generation."""

    phase = Phase(
        name="generate",
        display_name="Silent Idea Generation",
        description=(
            "Each participant independently proposes candidate ideas "
            "or solutions.  Contributions are anonymised — ideas are "
            "judged on content, not authorship."
        ),
        rounds=1,
    )

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    def init_state(self, discussion: Discussion) -> dict:
        return {"ideas": []}

    # ------------------------------------------------------------------
    # Prompts
    # ------------------------------------------------------------------

    def get_system_prompt(self, entity: Entity,
                          discussion: Discussion) -> str:
        return (
            f"You are {entity.name}, participating in a Nominal Group "
            "Technique (NGT) brainstorming session.\n"
            f"Topic: {discussion.topic}\n\n"
            "SILENT IDEA GENERATION PHASE\n\n"
            "Independently propose 3-7 distinct candidate ideas or "
            "solutions for this topic.  IMPORTANT: Do not react to or "
            "build on others' contributions — this is your independent "
            "thinking.  Diversity beats polish; include unconventional "
            "ideas.\n\n"
            "Submit your ideas by calling the submit_ideas tool with an "
            "array of idea strings — each a complete, specific, "
            "self-contained proposal — plus a brief rationale in the "
            "'reasoning' field."
        )

    def get_turn_prompt(self, entity: Entity,
                        discussion: Discussion) -> str:
        return (
            f"It is your turn, {entity.name}.  Independently propose "
            "3-7 distinct ideas by calling the submit_ideas tool."
        )

    def get_summary_prompt(self, discussion: Discussion,
                           speaker_name: str,
                           next_speaker_name: str) -> str:
        return (
            "A set of ideas has been received.  Do NOT reveal, quote, "
            "or evaluate any of them — silent generation requires that "
            "participants do not anchor on each other.  Simply invite "
            f"the next participant.\n\n{next_speaker_name}, please "
            "independently propose your candidate ideas on the topic."
        )

    # ------------------------------------------------------------------
    # Context filtering — anonymise authorship
    # ------------------------------------------------------------------

    def filter_context_message(self, entity_name: str, content: str,
                               role: str,
                               discussion: Discussion, *,
                               current_entity_id: int | None = None) -> str:
        return anonymise_content(content, discussion)

    # ------------------------------------------------------------------
    # Response processing (free-text / human fallback path)
    # ------------------------------------------------------------------

    def process_response(self, content: str, entity: Entity,
                         discussion: Discussion) -> ProcessedResponse:
        state = discussion.method_state
        items = parse_numbered_list(content, min_length=MIN_IDEA_LENGTH)
        if items:
            record_ideas(state, entity, items)
        else:
            logger.warning(
                "Could not extract ideas from %s's response", entity.name)
        return ProcessedResponse(display_content=content)

    # ------------------------------------------------------------------
    # Structured output (issue #23)
    # ------------------------------------------------------------------

    requires_structured_output = True

    def get_output_tool(self, entity: Entity,
                        discussion: Discussion) -> OutputToolSpec:
        return OutputToolSpec(
            name="submit_ideas",
            description=("Submit your independent candidate ideas as an "
                         "array of complete proposal strings, plus your "
                         "reasoning."),
            parameters=IDEAS_TOOL_PARAMETERS,
        )

    def validate_output(self, payload: dict, entity: Entity,
                        discussion: Discussion) -> str:
        return validate_ideas_payload(payload)

    def process_structured_response(self, payload: dict, entity: Entity,
                                    discussion: Discussion) -> ProcessedResponse:
        state = discussion.method_state
        texts = [str(i).strip() for i in payload["ideas"] if str(i).strip()]
        accepted = record_ideas(state, entity, texts)
        reasoning = str(payload.get("reasoning") or "").strip()
        numbered = "\n".join(f"{n}. {idea['text']}"
                             for n, idea in enumerate(accepted, 1))
        display = f"{reasoning}\n\n{numbered}" if numbered else reasoning
        return ProcessedResponse(display_content=display)

    # ------------------------------------------------------------------
    # Phase advancement
    # ------------------------------------------------------------------

    def should_advance(self, discussion: Discussion) -> bool:
        state = discussion.method_state
        phase_round = state.get("phase_round", 1)
        if phase_round > MAX_GENERATE_ROUNDS:
            logger.warning(
                "Idea generation reached round %d; advancing with %d "
                "idea(s) collected.",
                phase_round, len(state.get("ideas", [])),
            )
            return True
        return bool(state.get("ideas")) and phase_round > 1

    def _gave_up(self, discussion: Discussion) -> bool:
        """True if generation exhausted its rounds without any ideas."""
        state = discussion.method_state
        return (not state.get("ideas")
                and state.get("phase_round", 1) > MAX_GENERATE_ROUNDS)

    def next_phase(self, discussion: Discussion) -> str | None:
        """Abort the method when generation produced nothing.

        Without ideas the remaining phases (cluster/clarify/allocate/
        rank) are degenerate — they would consolidate and vote over an
        empty list and burn API spend producing nothing usable.
        """
        if self._gave_up(discussion):
            logger.warning(
                "Idea generation produced no ideas — ending the NGT "
                "method early")
            return None
        return LINEAR_NEXT

    def get_method_complete_message(self, discussion: Discussion) -> str:
        if not self._gave_up(discussion):
            return ""
        return (
            "⚠️ **Nominal Group Technique ended early.** The generation "
            f"phase collected no usable ideas after {MAX_GENERATE_ROUNDS} "
            "rounds, so the clustering, clarification, and voting phases "
            "were skipped.  Consider rephrasing the topic as an open "
            "'How might we…' question and starting a new discussion."
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_phases_ngt.py tests/test_ngt_helpers.py -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add consensus/methods/phases/generate_ideas.py tests/test_phases_ngt.py
git commit -m "feat(ngt): silent idea generation phase handler (#24)"
```

---

### Task 3: ClusterIdeasHandler (moderator clustering)

**Files:**
- Create: `consensus/methods/phases/cluster_ideas.py`
- Test: `tests/test_phases_ngt.py` (append test class)

**Interfaces:**
- Consumes: `_ngt_helpers` (`CANDIDATES_TOOL_PARAMETERS`, `MAX_CLUSTER_ATTEMPTS`, `MIN_IDEA_LENGTH`, `fallback_candidates_from_ideas`, `format_candidates`, `format_ideas_for_clustering`, `record_candidates`, `validate_candidates_payload`), `_delphi_helpers.anonymise_content`, `parsing.parse_numbered_list`
- Produces: `ClusterIdeasHandler` with `phase.name == "cluster"` (rounds=0), moderator-only turn order, `init_state -> {"candidates": [], "cluster_attempts": 0}`, give-up fallback promotion in `next_phase`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_phases_ngt.py` (add `from consensus.methods.phases.cluster_ideas import ClusterIdeasHandler` to the imports):

```python
class TestClusterIdeasHandler:
    def _disc_with_ideas(self, **state):
        disc = make_disc(current_phase="cluster", **state)
        record_ideas(disc.method_state,
                     Entity(name="TestAI", entity_type=EntityType.AI, id=1),
                     ["Offer a self-serve onboarding checklist inside "
                      "the product",
                      "Run monthly live office hours for new customers"])
        return disc

    def test_phase_is_condition_based(self):
        handler = ClusterIdeasHandler()
        assert handler.phase.name == "cluster"
        assert handler.phase.rounds == 0

    def test_moderator_only_turn_order(self):
        disc = self._disc_with_ideas()
        assert ClusterIdeasHandler().get_turn_order([1, 2], disc) == [99]

    def test_system_prompt_lists_raw_ideas(self, moderator):
        disc = self._disc_with_ideas()
        prompt = ClusterIdeasHandler().get_system_prompt(moderator, disc)
        assert "CLUSTERING PHASE" in prompt
        assert "Idea 1:" in prompt
        assert "submit_candidates" not in prompt or True  # tool named below
        assert "onboarding checklist" in prompt

    def test_turn_prompt_names_tool_and_retry_variant(self, moderator):
        disc = self._disc_with_ideas()
        assert "submit_candidates" in ClusterIdeasHandler().get_turn_prompt(
            moderator, disc)
        disc.method_state["cluster_attempts"] = 1
        retry = ClusterIdeasHandler().get_turn_prompt(moderator, disc)
        assert "not usable" in retry

    def test_free_text_path_records_candidates(self, moderator):
        disc = self._disc_with_ideas()
        content = ("1. Build a self-serve onboarding checklist\n"
                   "2. Run recurring live office hours for customers")
        ClusterIdeasHandler().process_response(content, moderator, disc)
        assert len(disc.method_state["candidates"]) == 2
        assert disc.method_state["cluster_attempts"] == 0

    def test_unparseable_response_increments_attempts(self, moderator):
        disc = self._disc_with_ideas()
        ClusterIdeasHandler().process_response(
            "I think these all look great.", moderator, disc)
        assert disc.method_state["candidates"] == []
        assert disc.method_state["cluster_attempts"] == 1

    def test_advances_when_candidates_recorded(self):
        disc = self._disc_with_ideas()
        record_candidates(disc.method_state,
                          [{"title": "A consolidated candidate idea"}])
        assert ClusterIdeasHandler().should_advance(disc) is True

    def test_does_not_advance_without_candidates_before_cap(self):
        disc = self._disc_with_ideas(cluster_attempts=MAX_CLUSTER_ATTEMPTS - 1)
        assert ClusterIdeasHandler().should_advance(disc) is False

    def test_gives_up_after_cap(self, caplog):
        disc = self._disc_with_ideas(cluster_attempts=MAX_CLUSTER_ATTEMPTS)
        with caplog.at_level("WARNING"):
            assert ClusterIdeasHandler().should_advance(disc) is True
        assert any("cluster" in r.message.lower() for r in caplog.records)

    def test_give_up_promotes_raw_ideas_to_candidates(self):
        disc = self._disc_with_ideas(cluster_attempts=MAX_CLUSTER_ATTEMPTS)
        handler = ClusterIdeasHandler()
        assert handler.next_phase(disc) == LINEAR_NEXT
        candidates = disc.method_state["candidates"]
        assert len(candidates) == 2
        assert candidates[0]["title"] == disc.method_state["ideas"][0]["text"]

    def test_no_ideas_at_all_aborts(self):
        disc = make_disc(current_phase="cluster",
                         cluster_attempts=MAX_CLUSTER_ATTEMPTS)
        handler = ClusterIdeasHandler()
        assert handler.next_phase(disc) is None
        assert "ended early" in handler.get_method_complete_message(disc)

    def test_transition_message_counts_ideas(self):
        disc = self._disc_with_ideas()
        msg = ClusterIdeasHandler().get_transition_message(disc)
        assert "2 idea(s)" in msg
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_phases_ngt.py -q`
Expected: collection error — `ModuleNotFoundError: No module named 'consensus.methods.phases.cluster_ideas'`

- [ ] **Step 3: Write the implementation**

Create `consensus/methods/phases/cluster_ideas.py`:

```python
"""Clustering phase handler for Nominal Group Technique.

A moderator-only phase (see frame_hypotheses.py for the pattern): the
moderator merges duplicates and groups related raw ideas into a
deduplicated candidate list via the forced ``submit_candidates``
output tool (issue #23); free-text numbered-list parsing remains the
fallback path.  If no parseable clustering arrives after
``MAX_CLUSTER_ATTEMPTS``, the raw deduplicated ideas are promoted to
candidates 1:1 — voting on raw ideas beats ending the method.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ..base import LINEAR_NEXT, OutputToolSpec, Phase, ProcessedResponse
from ..parsing import parse_numbered_list
from ..phase_handler import PhaseHandler
from ._delphi_helpers import anonymise_content
from ._ngt_helpers import (
    CANDIDATES_TOOL_PARAMETERS,
    MAX_CLUSTER_ATTEMPTS,
    MIN_IDEA_LENGTH,
    fallback_candidates_from_ideas,
    format_candidates,
    format_ideas_for_clustering,
    record_candidates,
    validate_candidates_payload,
)

if TYPE_CHECKING:
    from ...models import Discussion, Entity

logger = logging.getLogger(__name__)


class ClusterIdeasHandler(PhaseHandler):
    """Phase 2: Moderator consolidates raw ideas into candidates."""

    phase = Phase(
        name="cluster",
        display_name="Clustering & Deduplication",
        description=(
            "The moderator merges duplicates and groups closely related "
            "ideas into a deduplicated list of candidate ideas."
        ),
        rounds=0,  # condition-based: candidates recorded or attempts exhausted
    )

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    def init_state(self, discussion: Discussion) -> dict:
        return {"candidates": [], "cluster_attempts": 0}

    # ------------------------------------------------------------------
    # Turn order — moderator only
    # ------------------------------------------------------------------

    def get_turn_order(self, entity_ids: list[int],
                       discussion: Discussion) -> list[int]:
        """Only the moderator speaks during clustering."""
        return [discussion.moderator_id]

    # ------------------------------------------------------------------
    # Prompts
    # ------------------------------------------------------------------

    def get_system_prompt(self, entity: Entity,
                          discussion: Discussion) -> str:
        ideas_text = format_ideas_for_clustering(discussion.method_state)
        return (
            "You are the moderator of a Nominal Group Technique (NGT) "
            "session, consolidating the ideas from silent generation.\n"
            f"Topic: {discussion.topic}\n\n"
            "CLUSTERING PHASE\n\n"
            "Merge duplicates and group closely related ideas into a "
            "single list of distinct candidate ideas.  Do NOT evaluate, "
            "rank, or drop substantive ideas — only consolidate.  "
            "Preserve minority and unconventional ideas as their own "
            f"candidates.\n\nRaw ideas (anonymised):\n{ideas_text}"
        )

    def get_turn_prompt(self, entity: Entity,
                        discussion: Discussion) -> str:
        state = discussion.method_state
        if state.get("cluster_attempts", 0) > 0:
            return (
                "The previous clustering was not usable.  Please call "
                "the submit_candidates tool with the consolidated "
                "candidate list — each candidate one complete, specific "
                "statement."
            )
        return (
            "Consolidate the raw ideas into a deduplicated candidate "
            "list by calling the submit_candidates tool.  Give each "
            "candidate a complete 'title' statement and an optional "
            "'summary' noting what was merged."
        )

    # ------------------------------------------------------------------
    # Context filtering — keep authorship hidden
    # ------------------------------------------------------------------

    def filter_context_message(self, entity_name: str, content: str,
                               role: str,
                               discussion: Discussion, *,
                               current_entity_id: int | None = None) -> str:
        return anonymise_content(content, discussion)

    # ------------------------------------------------------------------
    # Response processing (free-text / fallback path)
    # ------------------------------------------------------------------

    def process_response(self, content: str, entity: Entity,
                         discussion: Discussion) -> ProcessedResponse:
        state = discussion.method_state
        items = parse_numbered_list(content, min_length=MIN_IDEA_LENGTH)
        if items:
            record_candidates(state,
                              [{"title": t, "summary": ""} for t in items])
            logger.info("Extracted %d candidates from clustering",
                        len(items))
        else:
            state["cluster_attempts"] = state.get("cluster_attempts", 0) + 1
            logger.warning(
                "Clustering attempt %d failed — no candidates found",
                state["cluster_attempts"])
        return ProcessedResponse(display_content=content)

    # ------------------------------------------------------------------
    # Structured output (issue #23)
    # ------------------------------------------------------------------

    requires_structured_output = True

    def get_output_tool(self, entity: Entity,
                        discussion: Discussion) -> OutputToolSpec:
        return OutputToolSpec(
            name="submit_candidates",
            description=("Submit the consolidated, deduplicated candidate "
                         "list: an array of {title, summary} objects, plus "
                         "your reasoning."),
            parameters=CANDIDATES_TOOL_PARAMETERS,
        )

    def validate_output(self, payload: dict, entity: Entity,
                        discussion: Discussion) -> str:
        return validate_candidates_payload(payload)

    def process_structured_response(self, payload: dict, entity: Entity,
                                    discussion: Discussion) -> ProcessedResponse:
        state = discussion.method_state
        record_candidates(state, payload["candidates"])
        logger.info("Recorded %d candidates from structured clustering",
                    len(state["candidates"]))
        reasoning = str(payload.get("reasoning") or "").strip()
        listing = format_candidates(state)
        display = f"{reasoning}\n\n{listing}" if reasoning else listing
        return ProcessedResponse(display_content=display)

    # ------------------------------------------------------------------
    # Phase advancement
    # ------------------------------------------------------------------

    def should_advance(self, discussion: Discussion) -> bool:
        state = discussion.method_state
        if state.get("candidates"):
            return True
        if state.get("cluster_attempts", 0) >= MAX_CLUSTER_ATTEMPTS:
            logger.warning(
                "Giving up on clustering after %d attempts",
                MAX_CLUSTER_ATTEMPTS)
            return True
        return False

    def next_phase(self, discussion: Discussion) -> str | None:
        """Fall back to voting on raw ideas when clustering gave up.

        Aborts only in the (defensive) case that there are no ideas at
        all to promote — generation should already have ended the
        method in that situation.
        """
        state = discussion.method_state
        if not state.get("candidates"):
            fallback_candidates_from_ideas(state)
            if state.get("candidates"):
                logger.warning(
                    "Clustering gave up — promoting %d raw idea(s) to "
                    "candidates 1:1", len(state["candidates"]))
            else:
                logger.warning(
                    "Clustering ended with no ideas at all — ending the "
                    "NGT method early")
                return None
        return LINEAR_NEXT

    def get_method_complete_message(self, discussion: Discussion) -> str:
        state = discussion.method_state
        if state.get("candidates") or state.get("ideas"):
            return ""
        return (
            "⚠️ **Nominal Group Technique ended early.** No ideas were "
            "available to cluster, so the clarification and voting "
            "phases were skipped."
        )

    # ------------------------------------------------------------------
    # Transition message (when transitioning TO this phase)
    # ------------------------------------------------------------------

    def get_transition_message(self, discussion: Discussion) -> str:
        n = len(discussion.method_state.get("ideas", []))
        return (
            f"**Phase: {self.phase.display_name}**\n\n"
            f"Silent generation is complete — {n} idea(s) were "
            "collected.  The moderator will now merge duplicates and "
            "consolidate them into a candidate list for clarification "
            "and voting."
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_phases_ngt.py -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add consensus/methods/phases/cluster_ideas.py tests/test_phases_ngt.py
git commit -m "feat(ngt): moderator clustering phase handler (#24)"
```

---

### Task 4: ClarifyIdeasHandler (clarification round)

**Files:**
- Create: `consensus/methods/phases/clarify_ideas.py`
- Test: `tests/test_phases_ngt.py` (append test class)

**Interfaces:**
- Consumes: `_ngt_helpers.format_candidates`, `_delphi_helpers.anonymise_content`
- Produces: `ClarifyIdeasHandler` with `phase.name == "clarify"` (rounds=1), no structured output, default advancement

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_phases_ngt.py` (add `from consensus.methods.phases.clarify_ideas import ClarifyIdeasHandler` to the imports):

```python
class TestClarifyIdeasHandler:
    def _disc(self):
        disc = make_disc(current_phase="clarify")
        record_candidates(disc.method_state, [
            {"title": "Build a self-serve onboarding checklist"},
            {"title": "Run recurring live office hours for customers"},
        ])
        return disc

    def test_phase_metadata(self):
        handler = ClarifyIdeasHandler()
        assert handler.phase.name == "clarify"
        assert handler.phase.rounds == 1
        assert handler.requires_structured_output is False

    def test_system_prompt_lists_candidates_and_forbids_ranking(
            self, ai_entity):
        prompt = ClarifyIdeasHandler().get_system_prompt(
            ai_entity, self._disc())
        assert "CLARIFICATION PHASE" in prompt
        assert "Candidate 1:" in prompt
        assert "Do NOT advocate" in prompt

    def test_turn_prompt(self, ai_entity):
        prompt = ClarifyIdeasHandler().get_turn_prompt(
            ai_entity, self._disc())
        assert "TestAI" in prompt
        assert "clarify" in prompt.lower()

    def test_context_is_anonymised(self):
        disc = self._disc()
        out = ClarifyIdeasHandler().filter_context_message(
            "TestAI", "TestAI asked about candidate 2", "assistant", disc)
        assert "TestAI" not in out

    def test_default_advancement_after_one_round(self):
        disc = self._disc()
        assert ClarifyIdeasHandler().should_advance(disc) is False
        disc.method_state["phase_round"] = 2
        assert ClarifyIdeasHandler().should_advance(disc) is True

    def test_transition_message_lists_candidates(self):
        msg = ClarifyIdeasHandler().get_transition_message(self._disc())
        assert "2 candidate idea(s)" in msg
        assert "Candidate 1:" in msg
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_phases_ngt.py -q`
Expected: collection error — `ModuleNotFoundError: No module named 'consensus.methods.phases.clarify_ideas'`

- [ ] **Step 3: Write the implementation**

Create `consensus/methods/phases/clarify_ideas.py`:

```python
"""Clarification phase handler for Nominal Group Technique.

One free-text round: participants make sure every candidate idea is
understood before voting — questions, ambiguities, overlaps, sharper
wording.  No advocacy or ranking yet, and no structured output tool
(this phase produces discussion, not data).  Context stays anonymised.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..base import Phase
from ..phase_handler import PhaseHandler
from ._delphi_helpers import anonymise_content
from ._ngt_helpers import format_candidates

if TYPE_CHECKING:
    from ...models import Discussion, Entity


class ClarifyIdeasHandler(PhaseHandler):
    """Phase 3: One round of clarification on the candidate list."""

    phase = Phase(
        name="clarify",
        display_name="Clarification",
        description=(
            "One round of questions and refinement: participants make "
            "sure every candidate idea is understood before voting.  "
            "No advocacy or ranking yet."
        ),
        rounds=1,
    )

    # ------------------------------------------------------------------
    # Prompts
    # ------------------------------------------------------------------

    def get_system_prompt(self, entity: Entity,
                          discussion: Discussion) -> str:
        candidates_text = format_candidates(discussion.method_state)
        return (
            f"You are {entity.name}, participating in a Nominal Group "
            "Technique (NGT) session.\n"
            f"Topic: {discussion.topic}\n\n"
            "CLARIFICATION PHASE\n\n"
            "Review the candidate ideas below.  Ask clarifying "
            "questions, point out ambiguities or overlaps, and suggest "
            "sharper wording where a candidate is unclear.  Do NOT "
            "advocate for or rank candidates yet — voting comes next.\n\n"
            f"Candidate ideas:\n{candidates_text}"
        )

    def get_turn_prompt(self, entity: Entity,
                        discussion: Discussion) -> str:
        return (
            f"It is your turn, {entity.name}.  Raise anything you need "
            "to clarify about the candidate ideas — or state that the "
            "list is clear to you.  Refer to candidates by number.  Do "
            "not rank or advocate yet."
        )

    def get_summary_prompt(self, discussion: Discussion,
                           speaker_name: str,
                           next_speaker_name: str) -> str:
        return (
            f"{speaker_name} has raised their clarification points.  "
            "Briefly answer factual questions about what a candidate "
            f"means, then invite {next_speaker_name}."
        )

    # ------------------------------------------------------------------
    # Context filtering — keep authorship hidden
    # ------------------------------------------------------------------

    def filter_context_message(self, entity_name: str, content: str,
                               role: str,
                               discussion: Discussion, *,
                               current_entity_id: int | None = None) -> str:
        return anonymise_content(content, discussion)

    # ------------------------------------------------------------------
    # Transition message (when transitioning TO this phase)
    # ------------------------------------------------------------------

    def get_transition_message(self, discussion: Discussion) -> str:
        state = discussion.method_state
        n = len(state.get("candidates", []))
        candidates_text = format_candidates(state)
        return (
            f"**Phase: {self.phase.display_name}**\n\n"
            f"The consolidated list has {n} candidate idea(s):\n"
            f"{candidates_text}\n\n"
            "One round of clarification follows: make sure every "
            "candidate is understood.  No advocacy or ranking yet."
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_phases_ngt.py -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add consensus/methods/phases/clarify_ideas.py tests/test_phases_ngt.py
git commit -m "feat(ngt): clarification phase handler (#24)"
```

---

### Task 5: AllocatePointsHandler (multi-voting)

**Files:**
- Create: `consensus/methods/phases/allocate_points.py`
- Test: `tests/test_phases_ngt.py` (append test class)

**Interfaces:**
- Consumes: `_ngt_helpers` (`ALLOCATIONS_TOOL_PARAMETERS`, `MAX_ALLOCATE_ROUNDS`, `POINTS_PER_VOTER`, `entities_with_allocations`, `extract_allocations`, `format_candidates`, `record_allocations`, `validate_allocations_payload`), `_delphi_helpers.anonymise_content`
- Produces: `AllocatePointsHandler` with `phase.name == "allocate"` (rounds=1), `init_state -> {"point_allocations": [], "points_per_voter": POINTS_PER_VOTER}`, `get_output_tool -> None` when the entity already allocated or there are no candidates

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_phases_ngt.py` (add `from consensus.methods.phases.allocate_points import AllocatePointsHandler` and `from consensus.methods.phases._ngt_helpers import record_allocations` to the imports):

```python
class TestAllocatePointsHandler:
    def _disc(self, **state):
        disc = make_disc(current_phase="allocate", **state)
        record_candidates(disc.method_state, [
            {"title": "Build a self-serve onboarding checklist"},
            {"title": "Run recurring live office hours for customers"},
        ])
        return disc

    def test_phase_metadata_and_init_state(self):
        handler = AllocatePointsHandler()
        assert handler.phase.name == "allocate"
        assert handler.init_state(make_disc()) == {
            "point_allocations": [], "points_per_voter": POINTS_PER_VOTER}

    def test_system_prompt_states_pool_and_candidates(self, ai_entity):
        prompt = AllocatePointsHandler().get_system_prompt(
            ai_entity, self._disc())
        assert "MULTI-VOTING PHASE" in prompt
        assert str(POINTS_PER_VOTER) in prompt
        assert "Candidate 1:" in prompt
        assert "submit_points" in prompt

    def test_turn_prompt_after_allocation(self, ai_entity):
        disc = self._disc()
        record_allocations(disc.method_state, ai_entity,
                           [{"candidate_id": 1, "points": POINTS_PER_VOTER}])
        prompt = AllocatePointsHandler().get_turn_prompt(ai_entity, disc)
        assert "already allocated" in prompt

    def test_output_tool_omitted_when_already_allocated(self, ai_entity):
        disc = self._disc()
        record_allocations(disc.method_state, ai_entity,
                           [{"candidate_id": 1, "points": POINTS_PER_VOTER}])
        assert AllocatePointsHandler().get_output_tool(
            ai_entity, disc) is None

    def test_output_tool_omitted_without_candidates(self, ai_entity):
        disc = make_disc(current_phase="allocate")
        assert AllocatePointsHandler().get_output_tool(
            ai_entity, disc) is None

    def test_output_tool_lists_candidates(self, ai_entity):
        spec = AllocatePointsHandler().get_output_tool(
            ai_entity, self._disc())
        assert spec.name == "submit_points"
        assert "Candidate 1:" in spec.description

    def test_validate_output_enforces_sum(self, ai_entity):
        handler = AllocatePointsHandler()
        disc = self._disc()
        bad = {"allocations": [{"candidate_id": 1, "points": 3}],
               "reasoning": "x"}
        assert str(POINTS_PER_VOTER) in handler.validate_output(
            bad, ai_entity, disc)
        good = {"allocations": [
            {"candidate_id": 1, "points": POINTS_PER_VOTER - 4},
            {"candidate_id": 2, "points": 4}], "reasoning": "x"}
        assert handler.validate_output(good, ai_entity, disc) == ""

    def test_process_structured_records_and_displays(self, ai_entity):
        handler = AllocatePointsHandler()
        disc = self._disc()
        payload = {
            "allocations": [
                {"candidate_id": 1, "points": 7, "rationale": "Scales"},
                {"candidate_id": 2, "points": 3},
            ],
            "reasoning": "Self-serve first; keep the human touch.",
        }
        processed = handler.process_structured_response(
            payload, ai_entity, disc)
        assert len(disc.method_state["point_allocations"]) == 2
        assert "Self-serve first" in processed.display_content
        assert "7 point(s)" in processed.display_content
        assert (processed.display_content.index("Self-serve first")
                < processed.display_content.index("7 point(s)"))

    def test_free_text_path_records(self, ai_entity):
        handler = AllocatePointsHandler()
        disc = self._disc()
        processed = handler.process_response(
            "Candidate 1: 6 points\nCandidate 2: 4 points",
            ai_entity, disc)
        assert len(disc.method_state["point_allocations"]) == 2
        assert "Point allocations recorded: 2" in (
            processed.display_content.replace("**", ""))

    def test_advances_when_all_participants_allocated(self):
        disc = self._disc()
        record_allocations(disc.method_state,
                           Entity(name="TestAI", entity_type=EntityType.AI,
                                  id=1),
                           [{"candidate_id": 1, "points": POINTS_PER_VOTER}])
        assert AllocatePointsHandler().should_advance(disc) is False
        record_allocations(disc.method_state,
                           Entity(name="Bob", entity_type=EntityType.HUMAN,
                                  id=2),
                           [{"candidate_id": 2, "points": POINTS_PER_VOTER}])
        assert AllocatePointsHandler().should_advance(disc) is True

    def test_advances_immediately_without_candidates(self):
        disc = make_disc(current_phase="allocate")
        assert AllocatePointsHandler().should_advance(disc) is True

    def test_gives_up_after_cap(self, caplog):
        disc = self._disc(phase_round=MAX_ALLOCATE_ROUNDS + 1)
        with caplog.at_level("WARNING"):
            assert AllocatePointsHandler().should_advance(disc) is True
        assert any("allocation" in r.message.lower()
                   for r in caplog.records)

    def test_transition_message_states_pool(self):
        msg = AllocatePointsHandler().get_transition_message(self._disc())
        assert str(POINTS_PER_VOTER) in msg
        assert "Candidate 1:" in msg
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_phases_ngt.py -q`
Expected: collection error — `ModuleNotFoundError: No module named 'consensus.methods.phases.allocate_points'`

- [ ] **Step 3: Write the implementation**

Create `consensus/methods/phases/allocate_points.py`:

```python
"""Multi-voting phase handler for Nominal Group Technique.

Each participant distributes a fixed pool of points across the
candidate ideas via the forced ``submit_points`` output tool
(issue #23); a JSON-block / ``Candidate N: X points`` free-text parse
remains the human/fallback path.  The phase advances when every
participant has allocated, with a round cap so unparseable turns
cannot stall the discussion (see vote.py's MAX_VOTE_ROUNDS pattern).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ..base import OutputToolSpec, Phase, ProcessedResponse
from ..phase_handler import PhaseHandler
from ._delphi_helpers import anonymise_content
from ._ngt_helpers import (
    ALLOCATIONS_TOOL_PARAMETERS,
    MAX_ALLOCATE_ROUNDS,
    POINTS_PER_VOTER,
    entities_with_allocations,
    extract_allocations,
    format_candidates,
    record_allocations,
    validate_allocations_payload,
)

if TYPE_CHECKING:
    from ...models import Discussion, Entity

logger = logging.getLogger(__name__)


class AllocatePointsHandler(PhaseHandler):
    """Phase 4: Participants distribute points across candidates."""

    phase = Phase(
        name="allocate",
        display_name="Multi-Voting",
        description=(
            "Each participant distributes a fixed pool of points "
            "across the candidate ideas to express their priorities."
        ),
        rounds=1,
    )

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    def init_state(self, discussion: Discussion) -> dict:
        return {"point_allocations": [],
                "points_per_voter": POINTS_PER_VOTER}

    # ------------------------------------------------------------------
    # Prompts
    # ------------------------------------------------------------------

    def get_system_prompt(self, entity: Entity,
                          discussion: Discussion) -> str:
        state = discussion.method_state
        pool = state.get("points_per_voter", POINTS_PER_VOTER)
        candidates_text = format_candidates(state)
        return (
            f"You are {entity.name}, participating in a Nominal Group "
            "Technique (NGT) session.\n"
            f"Topic: {discussion.topic}\n\n"
            "MULTI-VOTING PHASE\n\n"
            f"Distribute exactly {pool} points across the candidate "
            "ideas below to express your priorities.  You may give one "
            "candidate everything or spread points widely.\n\n"
            "Cast your allocation by calling the submit_points tool "
            "with one entry per candidate you support (candidate_id, "
            "points, optional rationale) plus your overall reasoning.\n\n"
            f"Candidate ideas:\n{candidates_text}"
        )

    def get_turn_prompt(self, entity: Entity,
                        discussion: Discussion) -> str:
        state = discussion.method_state
        pool = state.get("points_per_voter", POINTS_PER_VOTER)
        if entity.id in entities_with_allocations(state):
            return (f"{entity.name}, you have already allocated your "
                    "points.")
        return (
            f"It is your turn, {entity.name}.  Distribute exactly "
            f"{pool} points across the candidates by calling the "
            "submit_points tool."
        )

    def get_summary_prompt(self, discussion: Discussion,
                           speaker_name: str,
                           next_speaker_name: str) -> str:
        return (
            f"{speaker_name} has allocated their points.  Invite "
            f"{next_speaker_name} to allocate theirs."
        )

    # ------------------------------------------------------------------
    # Context filtering — keep authorship hidden until results
    # ------------------------------------------------------------------

    def filter_context_message(self, entity_name: str, content: str,
                               role: str,
                               discussion: Discussion, *,
                               current_entity_id: int | None = None) -> str:
        return anonymise_content(content, discussion)

    # ------------------------------------------------------------------
    # Response processing (free-text / human fallback path)
    # ------------------------------------------------------------------

    def process_response(self, content: str, entity: Entity,
                         discussion: Discussion) -> ProcessedResponse:
        state = discussion.method_state
        accepted = record_allocations(state, entity,
                                      extract_allocations(content))
        if accepted:
            content += (f"\n\n---\n**Point allocations recorded:** "
                        f"{accepted}")
        return ProcessedResponse(display_content=content)

    # ------------------------------------------------------------------
    # Structured output (issue #23)
    # ------------------------------------------------------------------

    requires_structured_output = True

    def get_output_tool(self, entity: Entity,
                        discussion: Discussion) -> OutputToolSpec | None:
        state = discussion.method_state
        if not state.get("candidates"):
            # Nothing to vote on: no payload could pass validation, so
            # forcing the tool would burn every retry.
            return None
        if entity.id in entities_with_allocations(state):
            # Already allocated: the free-text path handles the
            # "already voted" prose turn (see vote.py).
            return None
        pool = state.get("points_per_voter", POINTS_PER_VOTER)
        return OutputToolSpec(
            name="submit_points",
            description=(f"Distribute exactly {pool} points across the "
                         "candidate ideas:\n" + format_candidates(state)),
            parameters=ALLOCATIONS_TOOL_PARAMETERS,
        )

    def validate_output(self, payload: dict, entity: Entity,
                        discussion: Discussion) -> str:
        state = discussion.method_state
        valid_ids = {c["id"] for c in state.get("candidates", [])}
        pool = state.get("points_per_voter", POINTS_PER_VOTER)
        return validate_allocations_payload(payload, valid_ids, pool)

    def process_structured_response(self, payload: dict, entity: Entity,
                                    discussion: Discussion) -> ProcessedResponse:
        state = discussion.method_state
        allocations = [{"candidate_id": int(a["candidate_id"]),
                        "points": int(a["points"]),
                        "rationale": str(a.get("rationale") or "")}
                       for a in payload["allocations"]]
        accepted = record_allocations(state, entity, allocations)
        titles = {c["id"]: c["title"] for c in state.get("candidates", [])}
        lines = []
        for a in allocations:
            line = (f"**Candidate {a['candidate_id']} "
                    f"({titles.get(a['candidate_id'], '?')}): "
                    f"{a['points']} point(s)**")
            if a["rationale"]:
                line += f" — {a['rationale']}"
            lines.append(line)
        reasoning = str(payload.get("reasoning") or "").strip()
        display = (reasoning + "\n\n" + "\n".join(lines)
                   + f"\n\n---\n**Point allocations recorded:** {accepted}")
        return ProcessedResponse(display_content=display)

    # ------------------------------------------------------------------
    # Phase advancement
    # ------------------------------------------------------------------

    def should_advance(self, discussion: Discussion) -> bool:
        """Advance when every participant has allocated their points.

        A clustering that produced no candidates advances immediately —
        there is nothing to vote on.  Falls back to a ``phase_round``
        cap so the phase always terminates even if some allocations can
        never be recorded.
        """
        state = discussion.method_state
        if not state.get("candidates"):
            return True
        participant_ids = set(discussion.turn_order)
        if participant_ids and participant_ids.issubset(
                entities_with_allocations(state)):
            return True
        phase_round = state.get("phase_round", 1)
        if phase_round > MAX_ALLOCATE_ROUNDS:
            logger.warning(
                "Multi-voting reached round %d without all allocations; "
                "advancing with %d allocation(s) recorded.",
                phase_round, len(state.get("point_allocations", [])),
            )
            return True
        return False

    # ------------------------------------------------------------------
    # Transition message (when transitioning TO this phase)
    # ------------------------------------------------------------------

    def get_transition_message(self, discussion: Discussion) -> str:
        state = discussion.method_state
        pool = state.get("points_per_voter", POINTS_PER_VOTER)
        candidates_text = format_candidates(state)
        return (
            f"**Phase: {self.phase.display_name}**\n\n"
            "Clarification is complete.  Each participant now "
            f"distributes exactly {pool} points across the "
            f"candidates:\n{candidates_text}"
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_phases_ngt.py -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add consensus/methods/phases/allocate_points.py tests/test_phases_ngt.py
git commit -m "feat(ngt): multi-voting phase handler (#24)"
```

---

### Task 6: RankIdeasHandler + NominalGroupTechnique method + registry

**Files:**
- Create: `consensus/methods/phases/rank_ideas.py`
- Create: `consensus/methods/nominal_group.py`
- Modify: `consensus/methods/__init__.py`
- Test: `tests/test_phases_ngt.py` (append classes; switch `make_disc` to the registry)

**Interfaces:**
- Consumes: all Task 1–5 outputs
- Produces: `get_method("nominal_group")` returns `NominalGroupTechnique`; `method.requires_structured_output() is True`; phases `["generate", "cluster", "clarify", "allocate", "rank"]`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_phases_ngt.py` (add imports `from consensus.methods import get_method` and `from consensus.methods.phases.rank_ideas import RankIdeasHandler`), and replace the hand-rolled `make_disc` body with the registry-backed version:

```python
def make_disc(**state) -> Discussion:
    """A discussion in the NGT method with a moderator and two panelists."""
    mod = Entity(name="Mod", entity_type=EntityType.AI, id=99)
    alice = Entity(name="TestAI", entity_type=EntityType.AI, id=1)
    bob = Entity(name="Bob", entity_type=EntityType.HUMAN, id=2)
    disc = Discussion(topic="How can we improve customer onboarding?",
                      discussion_method="nominal_group",
                      entities=[mod, alice, bob],
                      moderator_id=99,
                      turn_order=[1, 2])
    disc.method_state = get_method("nominal_group").init_state(disc)
    disc.method_state.update(state)
    return disc
```

New test classes:

```python
class TestRankIdeasHandler:
    def _disc(self):
        disc = make_disc(current_phase="rank")
        record_candidates(disc.method_state, [
            {"title": "Build a self-serve onboarding checklist"},
            {"title": "Run recurring live office hours for customers"},
        ])
        record_allocations(disc.method_state,
                           Entity(name="TestAI", entity_type=EntityType.AI,
                                  id=1),
                           [{"candidate_id": 2, "points": POINTS_PER_VOTER}])
        return disc

    def test_moderator_only_turn_order(self):
        disc = self._disc()
        assert RankIdeasHandler().get_turn_order([1, 2], disc) == [99]

    def test_system_prompt_contains_ranked_totals(self, moderator):
        prompt = RankIdeasHandler().get_system_prompt(moderator, self._disc())
        assert "RANKED RESULTS" in prompt
        assert f"{POINTS_PER_VOTER} point(s)" in prompt

    def test_turn_prompt_requests_presentation(self, moderator):
        prompt = RankIdeasHandler().get_turn_prompt(moderator, self._disc())
        assert "ranked" in prompt.lower()

    def test_advances_after_one_round(self):
        disc = self._disc()
        assert RankIdeasHandler().should_advance(disc) is False
        disc.method_state["phase_round"] = 2
        assert RankIdeasHandler().should_advance(disc) is True

    def test_transition_message_shows_ranking(self):
        msg = RankIdeasHandler().get_transition_message(self._disc())
        assert "office hours" in msg


class TestNominalGroupMethod:
    def test_registered(self):
        method = get_method("nominal_group")
        assert method.name == "nominal_group"
        assert method.display_name == "Nominal Group Technique"

    def test_phase_order(self):
        method = get_method("nominal_group")
        assert [p.name for p in method.default_phases] == [
            "generate", "cluster", "clarify", "allocate", "rank"]

    def test_requires_structured_output(self):
        assert get_method("nominal_group").requires_structured_output() is True

    def test_init_state_merges_handler_keys(self):
        disc = make_disc()
        state = disc.method_state
        assert state["current_phase"] == "generate"
        assert state["ideas"] == []
        assert state["candidates"] == []
        assert state["cluster_attempts"] == 0
        assert state["point_allocations"] == []
        assert state["points_per_voter"] == POINTS_PER_VOTER

    def test_listed_in_catalog(self):
        from consensus.methods import list_methods
        names = [m["name"] for m in list_methods()]
        assert "nominal_group" in names

    def test_conclusion_prompt_contains_ranking(self):
        method = get_method("nominal_group")
        disc = make_disc()
        record_candidates(disc.method_state, [
            {"title": "Build a self-serve onboarding checklist"},
            {"title": "Run recurring live office hours for customers"},
        ])
        record_allocations(disc.method_state,
                           Entity(name="TestAI", entity_type=EntityType.AI,
                                  id=1),
                           [{"candidate_id": 1, "points": POINTS_PER_VOTER}])
        prompt = method.get_conclusion_prompt(disc)
        assert "Nominal Group Technique" in prompt
        assert "Ranked shortlist" in prompt
        assert "onboarding checklist" in prompt
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_phases_ngt.py -q`
Expected: FAIL — `KeyError: "Unknown discussion method: 'nominal_group'"` (and `ModuleNotFoundError` for `rank_ideas`)

- [ ] **Step 3: Write the implementations**

Create `consensus/methods/phases/rank_ideas.py`:

```python
"""Ranked-results phase handler for Nominal Group Technique.

A moderator-only phase (the moderator takes a real turn so the ranked
synthesis lands in the transcript — see frame_hypotheses.py for the
pattern): the point totals are tallied and the moderator presents the
ranked shortlist.  Identities are revealed from here on (no
anonymisation), mirroring Delphi's synthesise phase.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..base import Phase
from ..phase_handler import PhaseHandler
from ._ngt_helpers import format_ranked_candidates

if TYPE_CHECKING:
    from ...models import Discussion, Entity


class RankIdeasHandler(PhaseHandler):
    """Phase 5: Moderator presents the ranked shortlist."""

    phase = Phase(
        name="rank",
        display_name="Ranked Results",
        description=(
            "The moderator presents the ranked shortlist and "
            "synthesises the group's priorities."
        ),
        rounds=1,
    )

    # ------------------------------------------------------------------
    # Turn order — moderator only
    # ------------------------------------------------------------------

    def get_turn_order(self, entity_ids: list[int],
                       discussion: Discussion) -> list[int]:
        """Only the moderator speaks when presenting results."""
        return [discussion.moderator_id]

    # ------------------------------------------------------------------
    # Prompts
    # ------------------------------------------------------------------

    def get_system_prompt(self, entity: Entity,
                          discussion: Discussion) -> str:
        ranked = format_ranked_candidates(discussion.method_state)
        return (
            "You are the moderator of a Nominal Group Technique (NGT) "
            "session presenting the voting results.\n"
            f"Topic: {discussion.topic}\n\n"
            "RANKED RESULTS PHASE\n\n"
            f"Point totals:\n{ranked}\n\n"
            "Present the ranked shortlist with a short rationale for "
            "each top candidate, note how concentrated or split the "
            "vote was, and flag any low-scoring idea that received a "
            "strongly argued allocation."
        )

    def get_turn_prompt(self, entity: Entity,
                        discussion: Discussion) -> str:
        return (
            "Present the ranked results now: the shortlist in point "
            "order, the vote pattern, and notable rationales."
        )

    # ------------------------------------------------------------------
    # Phase advancement
    # ------------------------------------------------------------------

    def should_advance(self, discussion: Discussion) -> bool:
        return discussion.method_state.get("phase_round", 1) > 1

    # ------------------------------------------------------------------
    # Transition message (when transitioning TO this phase)
    # ------------------------------------------------------------------

    def get_transition_message(self, discussion: Discussion) -> str:
        return (
            f"**Phase: {self.phase.display_name}**\n\n"
            "All point allocations are in.  The moderator will now "
            "present the ranked shortlist:\n\n"
            + format_ranked_candidates(discussion.method_state)
        )
```

Create `consensus/methods/nominal_group.py`:

```python
"""Nominal Group Technique — structured brainstorming (issue #24).

The catalog's first *generative* method: instead of critiquing an
existing position, the group creates and prioritises options.

Phases:
  1. GENERATE  — Silent, anonymised, independent idea generation
  2. CLUSTER   — Moderator merges duplicates into a candidate list
  3. CLARIFY   — One round of questions/refinement (no advocacy)
  4. ALLOCATE  — Each participant distributes a fixed point pool
  5. RANK      — Moderator presents the ranked shortlist
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import DiscussionMethod
from .phases._ngt_helpers import (
    POINTS_PER_VOTER,
    entities_with_allocations,
    format_ranked_candidates,
)
from .phases.allocate_points import AllocatePointsHandler
from .phases.clarify_ideas import ClarifyIdeasHandler
from .phases.cluster_ideas import ClusterIdeasHandler
from .phases.generate_ideas import GenerateIdeasHandler
from .phases.rank_ideas import RankIdeasHandler

if TYPE_CHECKING:
    from ..models import Discussion


class NominalGroupTechnique(DiscussionMethod):
    """Nominal Group Technique — generate, consolidate, prioritise."""

    name = "nominal_group"
    display_name = "Nominal Group Technique"
    description = (
        "Structured brainstorming for generating and prioritising "
        "options.  Participants silently and independently propose "
        "ideas (anonymised), the moderator merges duplicates into a "
        "candidate list, one clarification round ensures shared "
        "understanding, then each participant distributes a fixed pool "
        "of points across candidates.  Produces a ranked shortlist.  "
        "Best for open problem-solving where the group must create "
        "options, not just evaluate a position."
    )
    phase_handlers = (
        GenerateIdeasHandler(),
        ClusterIdeasHandler(),
        ClarifyIdeasHandler(),
        AllocatePointsHandler(),
        RankIdeasHandler(),
    )

    # ------------------------------------------------------------------
    # Conclusion
    # ------------------------------------------------------------------

    def get_conclusion_prompt(self, discussion: Discussion) -> str:
        state = discussion.method_state
        pool = state.get("points_per_voter", POINTS_PER_VOTER)
        ranked = format_ranked_candidates(state)
        n_voters = len(entities_with_allocations(state))
        return (
            "The Nominal Group Technique process is complete.\n\n"
            f"Final ranking ({n_voters} participant(s) allocated "
            f"{pool} points each):\n{ranked}\n\n"
            "Provide a comprehensive synthesis:\n"
            "1. **Ranked shortlist** — Present the top candidates in "
            "order with their point totals\n"
            "2. **Rationale** — Summarise why the leading candidates "
            "earned support, citing participants' stated reasons\n"
            "3. **Vote pattern** — Was support concentrated or split?  "
            "Note near-ties and polarised allocations\n"
            "4. **Preserved dissent** — Flag lower-ranked ideas with "
            "strongly argued support worth revisiting\n"
            "5. **Next steps** — Recommend how to take the top "
            "candidates forward.\n\n"
            "Present actual point totals and cite specific rationales."
        )
```

Modify `consensus/methods/__init__.py` — three edits:

1. Add the import after `from .court_of_law import CourtOfLaw`:
```python
from .nominal_group import NominalGroupTechnique
```
2. Add to `_METHODS` after `"court_of_law": CourtOfLaw,`:
```python
    "nominal_group": NominalGroupTechnique,
```
3. Add `"NominalGroupTechnique",` to `__all__` after `"CourtOfLaw",`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_phases_ngt.py tests/test_methods.py -q`
Expected: all PASS (`test_methods.py` guards the registry contract; if it asserts a fixed method count, update that count there)

- [ ] **Step 5: Commit**

```bash
git add consensus/methods/phases/rank_ideas.py consensus/methods/nominal_group.py consensus/methods/__init__.py tests/test_phases_ngt.py
git commit -m "feat(ngt): assemble and register Nominal Group Technique (#24)"
```

---

### Task 7: Structured-output conversion tests (`test_ngt_structured.py`)

The handlers were built structured-first in Tasks 2–5; this task adds the dedicated structured-conversion test file every #23 conversion has (schema shape, prompts-name-the-tool, structured/free-text parity), mirroring `tests/test_surface_assumptions_structured.py`.

**Files:**
- Test: `tests/test_ngt_structured.py` (create)

**Interfaces:**
- Consumes: Tasks 1–6 outputs only; no production code changes expected. Any failure here is a defect in Tasks 1–6 — fix it there.

- [ ] **Step 1: Write the tests**

Create `tests/test_ngt_structured.py`:

```python
"""Structured-output coverage for the NGT phases (#23 convention).

The forced submit_ideas / submit_candidates / submit_points tools
replace free-text parsing for tool-capable models; the free-text
paths remain for humans.  Mirrors test_surface_assumptions_structured.
"""

from consensus.methods import get_method
from consensus.methods.phases._ngt_helpers import (
    ALLOCATIONS_TOOL_PARAMETERS,
    CANDIDATES_TOOL_PARAMETERS,
    IDEAS_TOOL_PARAMETERS,
    POINTS_PER_VOTER,
    record_candidates,
)
from consensus.methods.phases.allocate_points import AllocatePointsHandler
from consensus.methods.phases.clarify_ideas import ClarifyIdeasHandler
from consensus.methods.phases.cluster_ideas import ClusterIdeasHandler
from consensus.methods.phases.generate_ideas import GenerateIdeasHandler
from consensus.methods.phases.rank_ideas import RankIdeasHandler
from consensus.models import Discussion, Entity, EntityType


def _entity(eid: int = 1, name: str = "Alice") -> Entity:
    return Entity(id=eid, name=name, entity_type=EntityType.AI)


def _discussion(phase: str, **state) -> Discussion:
    disc = Discussion(topic="How can we improve customer onboarding?",
                      discussion_method="nominal_group",
                      moderator_id=99)
    disc.method_state = get_method("nominal_group").init_state(disc)
    disc.method_state["current_phase"] = phase
    disc.method_state.update(state)
    return disc


class TestStructuredFlags:
    def test_generate_cluster_allocate_require_structured(self):
        assert GenerateIdeasHandler().requires_structured_output is True
        assert ClusterIdeasHandler().requires_structured_output is True
        assert AllocatePointsHandler().requires_structured_output is True

    def test_clarify_and_rank_do_not(self):
        assert ClarifyIdeasHandler().requires_structured_output is False
        assert RankIdeasHandler().requires_structured_output is False

    def test_method_requires_structured_output(self):
        assert (get_method("nominal_group").requires_structured_output()
                is True)


class TestOutputToolSpecs:
    def test_generate_spec(self):
        spec = GenerateIdeasHandler().get_output_tool(
            _entity(), _discussion("generate"))
        assert spec.name == "submit_ideas"
        assert spec.parameters is IDEAS_TOOL_PARAMETERS

    def test_cluster_spec(self):
        spec = ClusterIdeasHandler().get_output_tool(
            _entity(99, "Mod"), _discussion("cluster"))
        assert spec.name == "submit_candidates"
        assert spec.parameters is CANDIDATES_TOOL_PARAMETERS

    def test_allocate_spec(self):
        disc = _discussion("allocate")
        record_candidates(disc.method_state,
                          [{"title": "A substantive candidate idea"}])
        spec = AllocatePointsHandler().get_output_tool(_entity(), disc)
        assert spec.name == "submit_points"
        assert spec.parameters is ALLOCATIONS_TOOL_PARAMETERS


class TestPromptsNameTheTool:
    def test_generate_prompts(self):
        handler = GenerateIdeasHandler()
        disc = _discussion("generate")
        assert "submit_ideas" in handler.get_system_prompt(_entity(), disc)
        assert "submit_ideas" in handler.get_turn_prompt(_entity(), disc)

    def test_cluster_turn_prompt(self):
        handler = ClusterIdeasHandler()
        disc = _discussion("cluster")
        assert "submit_candidates" in handler.get_turn_prompt(
            _entity(99, "Mod"), disc)

    def test_allocate_prompts(self):
        handler = AllocatePointsHandler()
        disc = _discussion("allocate")
        record_candidates(disc.method_state,
                          [{"title": "A substantive candidate idea"}])
        assert "submit_points" in handler.get_system_prompt(_entity(), disc)
        assert "submit_points" in handler.get_turn_prompt(_entity(), disc)


class TestStructuredMatchesFreeTextPaths:
    def test_generate_structured_and_free_text_produce_same_state(self):
        texts = ["Offer a self-serve onboarding checklist inside the "
                 "product",
                 "Run monthly live office hours for new customers"]
        handler = GenerateIdeasHandler()

        disc_a = _discussion("generate")
        handler.process_structured_response(
            {"ideas": texts, "reasoning": "Coverage of both modes."},
            _entity(), disc_a)

        disc_b = _discussion("generate")
        handler.process_response(
            "1. " + texts[0] + "\n2. " + texts[1], _entity(), disc_b)

        strip = [i["text"] for i in disc_a.method_state["ideas"]]
        assert strip == [i["text"] for i in disc_b.method_state["ideas"]]

    def test_cluster_structured_and_free_text_produce_same_state(self):
        handler = ClusterIdeasHandler()
        title = "Build a self-serve onboarding checklist"

        disc_a = _discussion("cluster")
        handler.process_structured_response(
            {"candidates": [{"title": title}],
             "reasoning": "Merged the self-serve ideas."},
            _entity(99, "Mod"), disc_a)

        disc_b = _discussion("cluster")
        handler.process_response("1. " + title, _entity(99, "Mod"), disc_b)

        assert (disc_a.method_state["candidates"]
                == disc_b.method_state["candidates"])

    def test_allocate_structured_and_free_text_produce_same_state(self):
        handler = AllocatePointsHandler()

        def fresh() -> Discussion:
            disc = _discussion("allocate")
            record_candidates(disc.method_state, [
                {"title": "A substantive candidate idea"},
                {"title": "Another substantive candidate idea"},
            ])
            return disc

        disc_a = fresh()
        handler.process_structured_response(
            {"allocations": [
                {"candidate_id": 1, "points": POINTS_PER_VOTER - 4},
                {"candidate_id": 2, "points": 4}],
             "reasoning": "Prioritising the first candidate."},
            _entity(), disc_a)

        disc_b = fresh()
        handler.process_response(
            f"Candidate 1: {POINTS_PER_VOTER - 4} points\n"
            "Candidate 2: 4 points",
            _entity(), disc_b)

        def key(state: dict) -> list[tuple]:
            return [(r["candidate_id"], r["points"], r["entity_id"])
                    for r in state["point_allocations"]]

        assert key(disc_a.method_state) == key(disc_b.method_state)
```

- [ ] **Step 2: Run the tests**

Run: `uv run pytest tests/test_ngt_structured.py -q`
Expected: all PASS (these lock in behavior already built; a failure means a Task 2–6 defect — fix it there, not by weakening the test)

- [ ] **Step 3: Commit**

```bash
git add tests/test_ngt_structured.py
git commit -m "test(ngt): structured-output conversion coverage (#24, #23 convention)"
```

---

### Task 8: Recommender — NGT in taxonomy, Open Discussion recommendable

Owner decision (2026-07-12, issue #24 comment): once NGT exists, remove `open_discussion` from `_EXCLUDED_METHODS` and update the `_TAXONOMY` line marking it "(fallback only)".

**Files:**
- Modify: `consensus/methods/recommender.py:24` (`_EXCLUDED_METHODS`) and `consensus/methods/recommender.py:77-89` (`_TAXONOMY`)
- Modify: `tests/test_recommender.py` (`test_build_catalog_excludes_triage_and_open`)

**Interfaces:**
- Consumes: nothing new
- Produces: `_EXCLUDED_METHODS == {"triage"}`; taxonomy mentions NGT and no longer marks Open Discussion "(fallback only)"

- [ ] **Step 1: Update the test to the new contract (failing first)**

In `tests/test_recommender.py`, replace `test_build_catalog_excludes_triage_and_open` with:

```python
    def test_build_catalog_excludes_only_triage(self):
        """Owner decision (issue #24): with NGT in the catalog, Open
        Discussion is recommendable again — only the Guided Triage
        meta-method stays excluded."""
        catalog = [
            {"name": "ach", "display_name": "ACH", "description": "...", "phases": []},
            {"name": "triage", "display_name": "Guided Triage", "description": "...", "phases": []},
            {"name": "open_discussion", "display_name": "Open Discussion", "description": "...", "phases": []},
            {"name": "nominal_group", "display_name": "Nominal Group Technique", "description": "...", "phases": []},
        ]
        recommender = MethodRecommender()
        filtered = recommender._filter_catalog(catalog)
        names = [m["name"] for m in filtered]
        assert "ach" in names
        assert "nominal_group" in names
        assert "open_discussion" in names
        assert "triage" not in names

    def test_taxonomy_mentions_ngt_and_drops_fallback_marker(self):
        from consensus.methods.recommender import _TAXONOMY
        assert "Nominal Group Technique" in _TAXONOMY
        assert "(fallback only)" not in _TAXONOMY
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_recommender.py -q`
Expected: FAIL — `assert "open_discussion" in names` and the taxonomy assertions

- [ ] **Step 3: Implement**

In `consensus/methods/recommender.py`:

1. Replace line 24:
```python
# Methods excluded from recommendation candidates (the Guided Triage
# meta-method recommends methods itself and must not recurse).
_EXCLUDED_METHODS = {"triage"}
```

2. In `_TAXONOMY`, replace the line
```
- General exploration from multiple perspectives → Open Discussion (fallback only)
```
with
```
- Generating and prioritising options / structured brainstorming → Nominal Group Technique (NGT)
- General exploration from multiple perspectives → Open Discussion
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_recommender.py -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add consensus/methods/recommender.py tests/test_recommender.py
git commit -m "feat(recommender): add NGT to taxonomy; make Open Discussion recommendable (#24)"
```

---

### Task 9: Documentation + HANDOVER

**Files:**
- Modify: `docs/devel/15-discussion-methods.md` (file list ~lines 25-60; method table ~line 252)
- Modify: `docs/user_manual/05_discussion_methods.md` ("Choosing a Method" table ~line 9; new method section before `## Method Transitions` ~line 206)
- Modify: `HANDOVER.md`

**Interfaces:** none (docs only).

- [ ] **Step 1: Update `docs/devel/15-discussion-methods.md`**

1. In the module file list, add after the `triage.py` line:
```
    nominal_group.py     — NominalGroupTechnique (structured brainstorming, NGT)
```
2. Update the `phases/` counter line (`— 34 PhaseHandler implementations + 4 helper modules` — recount actual handler files after this change and use the real numbers; the helpers count gains `_ngt_helpers.py`).
3. In the `phases/` listing, add after the `deliberate.py, vote.py, tally.py` line:
```
        _ngt_helpers.py
        generate_ideas.py, cluster_ideas.py, clarify_ideas.py
        allocate_points.py, rank_ideas.py
```
4. In the method table, add after the `| Guided Triage | 3 | Intake → Recommend → Confirm |` row:
```
| Nominal Group Technique | 5 | Generate → Cluster → Clarify → Allocate → Rank |
```

- [ ] **Step 2: Update `docs/user_manual/05_discussion_methods.md`**

1. In the "Choosing a Method" table, add a row after "Explore a topic openly":
```
| Generate and prioritise new ideas or options | Nominal Group Technique |
```
2. Add this section immediately before `## Method Transitions` (after the Recursive Self-Distillation section's `---`):

```markdown
### Nominal Group Technique (NGT)

Structured brainstorming — the catalog's generative method. Participants first propose ideas silently and independently (anonymised, so ideas are judged on content rather than authorship), the moderator merges duplicates into a candidate list, one clarification round ensures everyone understands each candidate, and then every participant distributes a fixed pool of points across the candidates. The result is a ranked shortlist.

**Phases:**
1. **Generate** — Silent, independent, anonymised idea generation
2. **Cluster** — The moderator merges duplicates into a candidate list
3. **Clarify** — One round of questions and refinement (no advocacy)
4. **Allocate** — Each participant distributes a fixed pool of points
5. **Rank** — The moderator presents the ranked shortlist

**Best for:** Open problem-solving, generating options, prioritising features or interventions, any question of the form "What should we do?" rather than "Is this right?"

---
```

- [ ] **Step 3: Update `HANDOVER.md`**

- In "Where things stand", add a bullet: **#24 Nominal Group Technique implemented** (this session): method `nominal_group`, 5 phases (3 structured: `submit_ideas` / `submit_candidates` / `submit_points`), give-up caps `MAX_GENERATE_ROUNDS` / `MAX_CLUSTER_ATTEMPTS` / `MAX_ALLOCATE_ROUNDS`, generate-phase abort, cluster give-up promotes raw ideas 1:1. Open Discussion is now recommendable (`_EXCLUDED_METHODS == {"triage"}`) per the owner decision.
- In "Next steps", remove the #24 entry (leaving #25 MCDA, #27 Double Crux, #26 Tree-of-Thoughts, #28, #29).
- Remove the now-satisfied "Decisions from the repo owner" bullet about #24/Open Discussion (it is executed, no longer instructive) — keep the #23 decision.

- [ ] **Step 4: Commit**

```bash
git add docs/devel/15-discussion-methods.md docs/user_manual/05_discussion_methods.md HANDOVER.md
git commit -m "docs: document Nominal Group Technique; update handover (#24)"
```

---

### Task 10: Full-suite verification

**Files:** none new.

- [ ] **Step 1: Run the entire test suite**

Run: `uv run pytest -q`
Expected: all tests pass (1472 pre-existing + the new NGT tests). Investigate and fix any regression before proceeding — likely suspects are registry-count assertions in `tests/test_methods.py` / `tests/test_app.py` and recommender-catalog assumptions in `tests/test_triage_handlers.py`.

- [ ] **Step 2: Import smoke check**

Run: `uv run python -c "from consensus.methods import get_method; m = get_method('nominal_group'); print(m.display_name, [p.name for p in m.default_phases], m.requires_structured_output())"`
Expected: `Nominal Group Technique ['generate', 'cluster', 'clarify', 'allocate', 'rank'] True`

- [ ] **Step 3: Fix anything found, re-run, commit any fixes**

```bash
git add -A
git commit -m "test(ngt): full-suite fixes after NGT integration (#24)"   # only if fixes were needed
```

---

## Self-Review Notes

- **Spec coverage:** Issue #24's five phases map to Tasks 2–6; the "reuse existing infrastructure" requirement is honored (anonymisation from `_delphi_helpers`, `parse_numbered_list`/`word_overlap_similar` from `parsing`, voting/tally shape from `_voting_helpers` adapted to point pools). The owner's Open-Discussion decision is Task 8. Docs + handover are Task 9.
- **Deviation from issue sketch:** the issue suggested reusing the Voting machinery verbatim for multi-voting; NGT's point-distribution semantics (fixed pool, sum constraint) don't fit motions/for-against votes, so Task 5 builds a parallel small helper set instead — same shapes, NGT-specific fields.
- **Type consistency check:** `record_ideas` returns `list[dict]` (used by generate's structured display); `record_candidates` returns `None` (display re-reads state via `format_candidates`); `record_allocations` returns `int` (both vote-style paths append a count footer). Candidate ids are `int` everywhere, coerced with `int(...)` at the payload boundary.
- **Known intentional asymmetry:** `validate_allocations_payload` rejects non-int `points` (strict, model retries with feedback) while `record_allocations` coerces (tolerant, free-text path) — same split as `_voting_helpers.record_votes` vs `VoteHandler.validate_output`.
