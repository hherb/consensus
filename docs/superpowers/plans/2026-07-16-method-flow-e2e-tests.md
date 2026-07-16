# Method-Flow E2E Tests Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Real-pipeline end-to-end flow tests driving NGT, MCDA, Double Crux, and Tree of Thoughts from `start_discussion` to `method_complete` through `submit_human_message` + `complete_turn`, including one real loop iteration per looping method.

**Architecture:** All-human roster (human moderator + 2–3 human participants); turn content carries method data through each handler's free-text `process_response` path; a shared bounded driver loop traces `(phase, speaker)` per turn. No network, no stubs, no monkeypatching. Spec: `docs/superpowers/specs/2026-07-16-method-flow-e2e-tests-design.md`.

**Tech Stack:** pytest + pytest-asyncio, existing `tmp_db` fixture (`tests/conftest.py`), production modules `consensus.app_discussion_setup`, `consensus.app_discussion_flow`, `consensus.moderator`, `consensus.pricing`.

## Global Constraints

- `uv` only — run tests with `uv run pytest` (never pip/python directly).
- No magic numbers: the turn budget is the module constant `MAX_E2E_TURNS = 40`.
- Docstrings and type hints mandatory; files ≤ ~500 lines (helpers live in `tests/flow_e2e_helpers.py`, tests in `tests/test_method_flow_e2e.py`).
- No production code changes in these tasks. If a run exposes a product bug: stop, investigate per superpowers:systematic-debugging, fix it in its **own** commit with its own regression test (golden rule 7), then resume.
- Never weaken an assertion to make a run pass — fix the turn-content script or (if the product is wrong) the product.
- `tests/__init__.py` exists, so the helper module is imported as `from tests.flow_e2e_helpers import ...`.

## Verified mechanics the tests rely on (all confirmed against source)

- `start_discussion(discussion, db, moderator)` creates the DB record, registers members, builds `turn_order`/`base_turn_order` from non-moderator entities, applies the first phase's turn order, and returns `{"started": True}` ([app_discussion_setup.py:323](consensus/app_discussion_setup.py#L323)). The issue-#23 tool gate only inspects AI models — an all-human roster passes (precedent: `test_triage_switch_seeds_full_roster`).
- Per turn: `submit_human_message` routes content through `method.process_response` (and evidence annotation for `track_evidence` phases); `complete_turn(..., moderator_summary=...)` stores the human moderator's summary, calls `advance_turn()` (index +1 modulo order, `turn_number` +1), fires `on_round_complete` on wrap (increments `phase_round`), then `should_advance_phase` → `advance_phase` (resets `phase_round` to 1, bumps `_phase_entries`) → `apply_method_turn_order(reset_index=True)`.
- `should_advance_phase` runs after **every** turn, so condition-based phases can end mid-round.
- Free-text formats per phase (handler `process_response`):
  - numbered lists (`1. …`, items ≥ min length): NGT `generate` (≥10 chars) & `cluster` (≥10), MCDA `options` (≥3), ToT `propose` (≥10); MCDA `criteria` additionally parses a `(weight: N)` suffix.
  - fenced ```json blocks: MCDA `score` (`{"scores": {"O1": {"C1": n}}}`), NGT `allocate` (also accepts `Candidate N: X points` lines; batch must sum to exactly `POINTS_PER_VOTER=10`, all-or-nothing), DC `hunt_cruxes` (`{"cruxes": [{claim, belief, why_pivotal}]}`), DC `identify_crux` (`{"verdict": …, "crux_ids": …, "claim": …, "reasoning": …}` — fenced JSON with `verdict` is the **only** accepted free-text form, and `reasoning` must be non-empty), DC `resolve` (`{"stance", "position", "crux_belief", "reasoning"}`), ToT `score` (`{"scores": {"T1": {"feasibility": n, "impact": n, "risk": n}}}` — all three dimensions required per label), ToT `expand` (`{"expansions": [{thought_id, refinement ≥10 chars, obstacles}]}`).
  - presentational phases extract nothing: NGT `clarify`/`rank`, MCDA `sensitivity`, ToT `prune`/`synthesise`. MCDA `decide` free text records a **fallback** `decision_artifact` (recommendation = top-ranked option, caveat "Recommendation defaulted to the top-ranked option (the moderator turn was free text).").
- Similarity clustering (`threshold 0.7` word overlap) merges near-duplicate ideas/options/criteria/thoughts across the whole raw set; same-name criteria from different entities are *meant* to merge (weight votes recorded per entity). Same-entity near-duplicate cruxes are dropped; cross-entity similarity is kept.
- ToT composite = `feasibility + impact + (6 − risk)`, meaned over scorers; beam = top `BEAM_WIDTH=3` (scored-before-unscored, composite desc, id asc). Prune pass 1 (no previous beam) always continues to `expand`; `expand.next_phase` returns `"score"` unconditionally; prune pass 2 converges only if the ordered beam is unchanged **and** every survivor was freshly re-scored in the pass (`scores_by_pass[str(current_depth)]`). Off-beam labels in a score payload are silently dropped, so the same score content works for both passes.
- DC identify verdict `"none"` with `crux_search_rounds(1) < MAX_CRUX_SEARCH_ROUNDS(3)` increments `crux_search_rounds`, resets the verdict, and jumps back to `"hunt_cruxes"`; verdict `"factual"` snapshots `initial_beliefs` from the **cited** cruxes and routes linearly to `test_crux` (rounds=2, `track_evidence=True`). `resolve.next_phase` builds `crux_map` then ends (last phase).
- Evidence annotation appends `"\n\n— sources: …"` (grounded, e.g. an `[evidence: URL]` marker) or `"\n\n— reasoning-based contribution (no cited evidence)"` to the stored content and logs to `method_state["evidence_log"]`.
- Phase names (verified): NGT `generate, cluster, clarify, allocate, rank`; MCDA `options, criteria, score, sensitivity, decide`; DC `positions, hunt_cruxes, identify_crux, test_crux, resolve`; ToT `propose, score, prune, expand, synthesise`.

---

### Task 1: Shared driver helpers + NGT E2E flow test

**Files:**
- Create: `tests/flow_e2e_helpers.py`
- Create: `tests/test_method_flow_e2e.py`

**Interfaces:**
- Produces (used by Tasks 2–4):
  - `flow_e2e_helpers.MAX_E2E_TURNS: int`
  - `flow_e2e_helpers.start_method_discussion(db, method_name: str, n_participants: int, topic: str) -> tuple[Discussion, Moderator, PricingCache, Entity, list[Entity]]`
  - `async flow_e2e_helpers.run_method(disc, moderator, db, pricing, content_for: Callable[[Discussion, Entity], str]) -> tuple[list[tuple[str, str]], dict]` — returns the `(phase, speaker_name)` trace and the final `complete_turn` result (guaranteed `method_complete: True`).
  - `flow_e2e_helpers.db_method_state(db, discussion_id: int) -> dict`

- [ ] **Step 1: Write the failing NGT test (and the test-file skeleton)**

Create `tests/test_method_flow_e2e.py`:

```python
"""Real-pipeline end-to-end flow tests for the four newest methods.

Each test drives one method from ``start_discussion`` to
``method_complete`` through the production pipeline
(``submit_human_message`` -> ``complete_turn``) with an all-human
roster: phase transitions, moderator-only turn-order handoffs, and (for
Double Crux / Tree of Thoughts) loop routing all run through the real
``should_advance_phase`` -> ``advance_phase`` -> ``apply_method_turn_order``
machinery.  Nothing is pre-seeded into ``method_state``.

Spec: docs/superpowers/specs/2026-07-16-method-flow-e2e-tests-design.md
"""

import pytest

from consensus.methods import get_method
from consensus.methods.phases._ngt_helpers import tally_points
from consensus.models import Discussion, Entity
from tests.flow_e2e_helpers import (
    db_method_state,
    run_method,
    start_method_discussion,
)

# ---------------------------------------------------------------------------
# Nominal Group Technique — straight line through all five phases
# ---------------------------------------------------------------------------

#: Two ideas per participant, pairwise word-overlap far below the 0.7
#: clustering threshold so all six survive as distinct ideas.
NGT_IDEAS = {
    "P1": "1. Build solar panel arrays across municipal rooftops\n"
          "2. Launch community composting hubs in every district",
    "P2": "1. Retrofit old buildings with modern insulation standards\n"
          "2. Create protected cycling corridors through downtown",
    "P3": "1. Deploy neighbourhood battery storage cooperatives\n"
          "2. Plant native drought-resistant trees along avenues",
}

#: The moderator's consolidation (numbered list, items >= 10 chars).
NGT_CANDIDATES = (
    "1. Municipal rooftop solar arrays\n"
    "2. Community composting hubs\n"
    "3. Building insulation retrofits\n"
    "4. Protected cycling corridors"
)

#: Each batch sums to exactly POINTS_PER_VOTER (10) — the free-text
#: allocation gate is all-or-nothing.  Totals: C1=11, C2=8, C4=6, C3=5.
NGT_ALLOCATIONS = {
    "P1": "Candidate 1: 6 points\nCandidate 2: 4 points",
    "P2": "Candidate 1: 5 points\nCandidate 3: 5 points",
    "P3": "Candidate 4: 6 points\nCandidate 2: 4 points",
}


def ngt_content(disc: Discussion, speaker: Entity) -> str:
    """Scripted turn content for the NGT run, keyed on the live phase."""
    phase = disc.method_state["current_phase"]
    if phase == "generate":
        return NGT_IDEAS[speaker.name]
    if phase == "cluster":
        return NGT_CANDIDATES
    if phase == "clarify":
        return ("Could the composting hubs accept commercial food waste "
                "as well as household waste?")
    if phase == "allocate":
        return NGT_ALLOCATIONS[speaker.name]
    if phase == "rank":
        return "Here is the final ranking based on your point allocations."
    pytest.fail(f"unexpected NGT phase {phase!r} for {speaker.name}")


class TestNominalGroupFlow:
    """NGT full lifecycle: generate -> cluster -> clarify -> allocate -> rank."""

    @pytest.mark.asyncio
    async def test_full_run(self, tmp_db):
        disc, moderator, pricing, mod, parts = start_method_discussion(
            tmp_db, "nominal_group", n_participants=3,
            topic="How can our city cut household carbon emissions?",
        )

        trace, result = await run_method(
            disc, moderator, tmp_db, pricing, ngt_content)

        # --- Flow ---------------------------------------------------
        assert result.get("method_complete") is True
        assert [phase for phase, _ in trace] == (
            ["generate"] * 3 + ["cluster"] + ["clarify"] * 3
            + ["allocate"] * 3 + ["rank"]
        )
        assert [name for phase, name in trace if phase == "generate"] == [
            "P1", "P2", "P3"]
        assert all(name == "Mod" for phase, name in trace
                   if phase in ("cluster", "rank")), (
            "moderator-only phases must be spoken by the moderator alone")

        # --- Artifacts ----------------------------------------------
        state = disc.method_state
        assert len(state["ideas"]) == 6, "all six distinct ideas recorded"
        assert [c["id"] for c in state["candidates"]] == [1, 2, 3, 4]
        assert tally_points(state) == {1: 11, 2: 8, 3: 5, 4: 6}

        # --- Conclusion prompt renders the real data -----------------
        prompt = get_method("nominal_group").get_conclusion_prompt(disc)
        assert "3 participant(s) allocated 10 points each" in prompt
        assert "Municipal rooftop solar arrays" in prompt
        assert "11 point(s) from 2 participant(s)" in prompt

        # --- Persistence sanity (issue #16 convention) ---------------
        persisted = db_method_state(tmp_db, disc.id)
        assert persisted["current_phase"] == state["current_phase"]
        assert persisted["candidates"] == state["candidates"]
```

- [ ] **Step 2: Run to verify it fails on the missing helper module**

Run: `uv run pytest tests/test_method_flow_e2e.py -v`
Expected: collection error — `ModuleNotFoundError: No module named 'tests.flow_e2e_helpers'`

- [ ] **Step 3: Implement the driver helpers**

Create `tests/flow_e2e_helpers.py`:

```python
"""Shared driver for the real-pipeline method-flow E2E tests.

Builds an all-human discussion through the production
``start_discussion`` and drives it turn by turn through
``submit_human_message`` + ``complete_turn`` (the human-moderator
summary path — no network, no stubs), tracing the ``(phase, speaker)``
of every turn until the method completes.

Spec: docs/superpowers/specs/2026-07-16-method-flow-e2e-tests-design.md
"""

from __future__ import annotations

import json
from typing import Callable

import pytest

from consensus.app_discussion_flow import complete_turn, submit_human_message
from consensus.app_discussion_setup import start_discussion
from consensus.database import Database
from consensus.models import Discussion, Entity
from consensus.moderator import Moderator
from consensus.pricing import PricingCache

#: Hard turn budget per E2E run — a flow regression must fail the test,
#: never hang the suite.  The longest scripted run (Double Crux) takes
#: 14 turns; 40 leaves headroom without masking a runaway loop.
MAX_E2E_TURNS = 40


def make_entity(db: Database, name: str) -> Entity:
    """Insert a human entity and return the loaded Entity."""
    eid = db.add_entity(name, "human", "#123456")
    return Entity.from_db_row(db.get_entity(eid))


def start_method_discussion(
    db: Database, method_name: str, n_participants: int, topic: str,
) -> tuple[Discussion, Moderator, PricingCache, Entity, list[Entity]]:
    """Start a real discussion: human moderator 'Mod' + P1..Pn.

    Everything (DB record, members, turn order, method init_state, the
    first phase's turn order) is set up by the production
    ``start_discussion`` — nothing is pre-seeded.
    """
    mod = make_entity(db, "Mod")
    parts = [make_entity(db, f"P{i + 1}") for i in range(n_participants)]
    disc = Discussion(
        topic=topic,
        entities=[mod] + parts,
        moderator_id=mod.id,
        discussion_method=method_name,
    )
    moderator = Moderator(disc, db)
    result = start_discussion(disc, db, moderator)
    assert result.get("started") is True, f"start_discussion failed: {result}"
    pricing = PricingCache(db.conn, db._lock)
    return disc, moderator, pricing, mod, parts


async def run_method(
    disc: Discussion, moderator: Moderator, db: Database,
    pricing: PricingCache,
    content_for: Callable[[Discussion, Entity], str],
) -> tuple[list[tuple[str, str]], dict]:
    """Drive turns until ``method_complete``; return (trace, final result).

    Each iteration submits the current speaker's scripted content (from
    ``content_for``, which reads the live ``method_state``) and completes
    the turn with a human-moderator summary.  Every step is asserted so
    a failure points at the exact turn; the ``MAX_E2E_TURNS`` budget
    turns a runaway loop into a failure with the full trace.
    """
    trace: list[tuple[str, str]] = []
    for _ in range(MAX_E2E_TURNS):
        speaker = disc.current_speaker
        assert speaker is not None, f"no current speaker; trace={trace}"
        phase = disc.method_state.get("current_phase", "")
        content = content_for(disc, speaker)
        submitted = submit_human_message(disc, db, speaker.id, content)
        assert "error" not in submitted, (
            f"submit failed in {phase!r} for {speaker.name}: {submitted}")
        result = await complete_turn(
            disc, moderator, db, pricing,
            get_state_fn=lambda: {},
            moderator_summary="Summary of the turn.",
        )
        assert "error" not in result, (
            f"complete_turn failed in {phase!r}: {result}")
        trace.append((phase, speaker.name))
        if result.get("method_complete"):
            return trace, result
    pytest.fail(
        f"method never completed within {MAX_E2E_TURNS} turns; "
        f"trace={trace}")


def db_method_state(db: Database, discussion_id: int) -> dict:
    """The persisted method_state, parsed from the discussion's DB row."""
    row = db.get_discussion(discussion_id)
    return json.loads(row.get("method_state") or "{}")
```

- [ ] **Step 4: Run the NGT test to verify it passes**

Run: `uv run pytest tests/test_method_flow_e2e.py -v`
Expected: `TestNominalGroupFlow::test_full_run PASSED`

If it fails: read the assert message (it names the phase and turn). A content-script mismatch (e.g. an idea merged by clustering) → fix the script constants. A pipeline behavior mismatch → suspect a real product bug: stop and follow the Global Constraints bug protocol.

- [ ] **Step 5: Run the neighboring flow suites to catch interference**

Run: `uv run pytest tests/test_method_flow_e2e.py tests/test_turn_order_flow.py tests/test_method_state_persistence.py tests/test_phase_machine_loops.py -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add tests/flow_e2e_helpers.py tests/test_method_flow_e2e.py
git commit -m "test: real-pipeline E2E flow driver + NGT full-lifecycle run"
```

---

### Task 2: MCDA E2E flow test

**Files:**
- Modify: `tests/test_method_flow_e2e.py` (append)

**Interfaces:**
- Consumes: `start_method_discussion`, `run_method`, `db_method_state` from `tests/flow_e2e_helpers` (Task 1 signatures above).

**Pre-computed expectations** (assertions below): criterion mean weights C1 `(4+2)/2 = 3.0`, C2 `3.0`, C3 `5.0`; cell means over the two scorers give weighted totals O1 `3.0*4.5 + 3.0*4.0 + 5.0*4.0 = 45.5`, O2 `26.5`, O3 `21.5` → O1 wins.

- [ ] **Step 1: Append the failing MCDA test**

Append to `tests/test_method_flow_e2e.py`:

```python
# ---------------------------------------------------------------------------
# Weighted Decision Matrix — straight line through all five phases
# ---------------------------------------------------------------------------

#: Distinct options (pairwise overlap ~0) -> O1, O2 from P1; O3 from P2.
MCDA_OPTIONS = {
    "P1": "1. Adopt PostgreSQL as the primary datastore\n"
          "2. Keep SQLite with a write-ahead log",
    "P2": "1. Move everything to a managed cloud database",
}

#: The '(weight: N)' suffix is the only parsed free-text weight form.
#: 'Operational cost' appears from both entities -> one criterion C1
#: with weight votes {P1: 4, P2: 2}.  Criteria rounds=2: the same lines
#: are resubmitted in round 2 (idempotent last-write-wins refinement).
MCDA_CRITERIA = {
    "P1": "1. Operational cost (weight: 4)\n"
          "2. Query performance (weight: 3)",
    "P2": "1. Operational cost (weight: 2)\n"
          "2. Migration effort (weight: 5)",
}

MCDA_SCORES = {
    "P1": ("My scores:\n```json\n"
           '{"scores": {'
           '"O1": {"C1": 5, "C2": 4, "C3": 4}, '
           '"O2": {"C1": 3, "C2": 3, "C3": 2}, '
           '"O3": {"C1": 2, "C2": 4, "C3": 1}}}'
           "\n```"),
    "P2": ("My scores:\n```json\n"
           '{"scores": {'
           '"O1": {"C1": 4, "C2": 4, "C3": 4}, '
           '"O2": {"C1": 3, "C2": 2, "C3": 2}, '
           '"O3": {"C1": 2, "C2": 3, "C3": 1}}}'
           "\n```"),
}


def mcda_content(disc: Discussion, speaker: Entity) -> str:
    """Scripted turn content for the MCDA run, keyed on the live phase."""
    phase = disc.method_state["current_phase"]
    if phase == "options":
        return MCDA_OPTIONS[speaker.name]
    if phase == "criteria":
        return MCDA_CRITERIA[speaker.name]
    if phase == "score":
        return MCDA_SCORES[speaker.name]
    if phase == "sensitivity":
        return ("The weighted ranking is robust: the leader holds under "
                "the tested weight variations.")
    if phase == "decide":
        return ("I recommend adopting PostgreSQL as the primary "
                "datastore based on the weighted results.")
    pytest.fail(f"unexpected MCDA phase {phase!r} for {speaker.name}")


class TestDecisionMatrixFlow:
    """MCDA full lifecycle: options -> criteria -> score -> sensitivity -> decide."""

    @pytest.mark.asyncio
    async def test_full_run(self, tmp_db):
        disc, moderator, pricing, mod, parts = start_method_discussion(
            tmp_db, "decision_matrix", n_participants=2,
            topic="Which datastore should the project standardise on?",
        )

        trace, result = await run_method(
            disc, moderator, tmp_db, pricing, mcda_content)

        # --- Flow ---------------------------------------------------
        assert result.get("method_complete") is True
        assert [phase for phase, _ in trace] == (
            ["options"] * 2 + ["criteria"] * 4 + ["score"] * 2
            + ["sensitivity"] + ["decide"]
        )
        assert all(name == "Mod" for phase, name in trace
                   if phase in ("sensitivity", "decide"))

        # --- Artifacts (all numbers computed in code) -----------------
        state = disc.method_state
        assert [o["id"] for o in state["options"]] == [1, 2, 3]
        weights = {c["id"]: sorted(c["weight_votes"].values())
                   for c in state["criteria"]}
        assert weights == {1: [2, 4], 2: [3], 3: [5]}

        artifact = state["decision_artifact"]
        assert artifact["recommended_option_id"] == 1
        assert artifact["ranking"][0]["option_id"] == 1
        assert artifact["ranking"][0]["weighted_total"] == 45.5
        assert [r["weighted_total"] for r in artifact["ranking"]] == [
            45.5, 26.5, 21.5]
        assert artifact["scorers"] == 2
        assert any("defaulted to the top-ranked option" in c
                   for c in artifact["caveats"]), (
            "free-text decide turn must record the default-caveat")

        # --- Conclusion prompt renders the real data -----------------
        prompt = get_method("decision_matrix").get_conclusion_prompt(disc)
        assert "Adopt PostgreSQL as the primary datastore" in prompt
        assert "45.5" in prompt

        # --- Persistence sanity ---------------------------------------
        persisted = db_method_state(tmp_db, disc.id)
        assert persisted["current_phase"] == state["current_phase"]
        assert (persisted["decision_artifact"]["recommended_option_id"]
                == artifact["recommended_option_id"])
```

- [ ] **Step 2: Run the new test**

Run: `uv run pytest tests/test_method_flow_e2e.py::TestDecisionMatrixFlow -v`
Expected: PASS (product code already exists; the test is the deliverable). On failure apply the Task 1 Step 4 triage: script bug → fix constants; product bug → bug protocol.

- [ ] **Step 3: Run the whole new file**

Run: `uv run pytest tests/test_method_flow_e2e.py -q`
Expected: 2 passed.

- [ ] **Step 4: Commit**

```bash
git add tests/test_method_flow_e2e.py
git commit -m "test: MCDA full-lifecycle E2E flow run"
```

---

### Task 3: Double Crux E2E flow test (identify loop-back)

**Files:**
- Modify: `tests/test_method_flow_e2e.py` (append)

**Interfaces:**
- Consumes: Task 1 helpers, unchanged.

**Loop choreography:** identify pass 1 issues verdict `none` → `next_phase` bumps `crux_search_rounds` to 2 and jumps back to `hunt_cruxes`; hunt round 2 submits fresh cruxes (ids 3, 4 — same polarity as the eventual shared claim so `initial_beliefs` are meaningful); identify pass 2 issues verdict `factual` citing `[3, 4]` → `test_crux` (2 rounds, evidence-tracked) → `resolve` builds `crux_map`. Belief shifts: P1 `0.75 → 0.7` (−0.05), P2 `0.25 → 0.6` (+0.35).

- [ ] **Step 1: Append the failing Double Crux test**

Append to `tests/test_method_flow_e2e.py`:

```python
# ---------------------------------------------------------------------------
# Double Crux — exercises the identify -> hunt loop-back (issue #22 path)
# ---------------------------------------------------------------------------

DC_POSITIONS = {
    "P1": "Remote-first work makes our engineering team more productive "
          "overall.",
    "P2": "Our engineering team loses more than it gains when it is "
          "fully remote.",
}

#: Round-1 cruxes deliberately do NOT overlap -> the moderator's first
#: identify pass returns verdict 'none' and loops back to hunting.
DC_HUNT_ROUND_1 = {
    "P1": ("```json\n"
           '{"cruxes": [{"claim": "Commute savings convert into extra '
           'focused work hours", "belief": 0.8, "why_pivotal": '
           '"Recovered time is the core of my case"}]}'
           "\n```"),
    "P2": ("```json\n"
           '{"cruxes": [{"claim": "Junior engineers ramp up slower '
           'without in-person mentoring", "belief": 0.9, "why_pivotal": '
           '"Mentoring quality drives my concern"}]}'
           "\n```"),
}

#: Round-2 cruxes share one pivotal claim (same polarity for both, so
#: the snapshot initial_beliefs are directly comparable) and are NOT
#: word-overlap similar to each author's own round-1 crux (same-entity
#: near-duplicates would be dropped).
DC_HUNT_ROUND_2 = {
    "P1": ("```json\n"
           '{"cruxes": [{"claim": "Distributed teams deliver features '
           'as fast as colocated teams", "belief": 0.75, "why_pivotal": '
           '"Delivery speed is what productivity means here"}]}'
           "\n```"),
    "P2": ("```json\n"
           '{"cruxes": [{"claim": "Feature delivery speed of distributed '
           'teams matches colocated teams", "belief": 0.25, '
           '"why_pivotal": "If speed holds up my objection collapses"}]}'
           "\n```"),
}

DC_IDENTIFY_NONE = (
    "```json\n"
    '{"verdict": "none", "reasoning": "The candidate cruxes address '
    'different mechanisms; no shared pivotal claim yet."}'
    "\n```"
)

DC_IDENTIFY_FACTUAL = (
    "```json\n"
    '{"verdict": "factual", "crux_ids": [3, 4], "claim": "Distributed '
    'teams deliver features as fast as colocated teams", "reasoning": '
    '"Both parties\' updated cruxes pivot on delivery speed."}'
    "\n```"
)

DC_TEST_CRUX = {
    "P1": "A 2024 multi-company study found delivery-speed parity for "
          "distributed teams [evidence: https://example.org/remote-study].",
    "P2": "In my direct experience, cross-team coordination overhead "
          "grows once a team is fully distributed.",
}

DC_RESOLUTIONS = {
    "P1": ("```json\n"
           '{"stance": "unchanged", "position": "Remote-first still nets '
           'out positive for our productivity", "crux_belief": 0.7, '
           '"reasoning": "The study supports delivery-speed parity"}'
           "\n```"),
    "P2": ("```json\n"
           '{"stance": "updated", "position": "I now think delivery '
           'speed is roughly comparable when distributed", '
           '"crux_belief": 0.6, "reasoning": "The cited study shifted '
           'my estimate upward"}'
           "\n```"),
}


def dc_content(disc: Discussion, speaker: Entity) -> str:
    """Scripted turn content for the Double Crux run.

    Reads the live ``crux_search_rounds`` to distinguish hunt round 1
    from round 2 and identify pass 1 from pass 2 — the loop-back is
    driven by real state, not a pre-scripted turn list.
    """
    state = disc.method_state
    phase = state["current_phase"]
    search_round = state.get("crux_search_rounds", 1)
    if phase == "positions":
        return DC_POSITIONS[speaker.name]
    if phase == "hunt_cruxes":
        return (DC_HUNT_ROUND_1 if search_round == 1
                else DC_HUNT_ROUND_2)[speaker.name]
    if phase == "identify_crux":
        return DC_IDENTIFY_NONE if search_round == 1 else DC_IDENTIFY_FACTUAL
    if phase == "test_crux":
        return DC_TEST_CRUX[speaker.name]
    if phase == "resolve":
        return DC_RESOLUTIONS[speaker.name]
    pytest.fail(f"unexpected Double Crux phase {phase!r} for {speaker.name}")


class TestDoubleCruxFlow:
    """Double Crux lifecycle incl. one identify->hunt loop iteration."""

    @pytest.mark.asyncio
    async def test_full_run_with_loop_back(self, tmp_db):
        disc, moderator, pricing, mod, parts = start_method_discussion(
            tmp_db, "double_crux", n_participants=2,
            topic="Should our engineering team stay remote-first?",
        )

        trace, result = await run_method(
            disc, moderator, tmp_db, pricing, dc_content)

        # --- Flow: the hunt/identify pair runs twice ------------------
        assert result.get("method_complete") is True
        assert [phase for phase, _ in trace] == (
            ["positions"] * 2
            + ["hunt_cruxes"] * 2 + ["identify_crux"]
            + ["hunt_cruxes"] * 2 + ["identify_crux"]
            + ["test_crux"] * 4 + ["resolve"] * 2
        )
        assert all(name == "Mod" for phase, name in trace
                   if phase == "identify_crux")

        # --- Loop + verdict state -------------------------------------
        state = disc.method_state
        assert state["crux_search_rounds"] == 2
        assert state["crux_verdict"] == "factual"
        assert state["shared_crux"]["claim"] == (
            "Distributed teams deliver features as fast as colocated teams")
        assert state["shared_crux"]["source_crux_ids"] == [3, 4]
        assert state["shared_crux"]["initial_beliefs"] == {
            "P1": 0.75, "P2": 0.25}

        # --- crux_map artifact (deterministic belief shifts) ----------
        crux_map = state["crux_map"]
        assert crux_map["verdict"] == "factual"
        assert crux_map["belief_shifts"]["P1"] == {
            "initial": 0.75, "final": 0.7, "shift": -0.05}
        assert crux_map["belief_shifts"]["P2"] == {
            "initial": 0.25, "final": 0.6, "shift": 0.35}
        assert crux_map["caveats"] == []

        # --- Evidence tracking on test_crux (issue #28) ---------------
        log = state["evidence_log"]
        assert len(log) == 4, "one entry per test_crux turn"
        assert [e["grounded"] for e in log] == [True, False, True, False]
        stored = [m.content for m in disc.messages]
        assert any("— sources:" in c and "example.org/remote-study" in c
                   for c in stored)
        assert any("reasoning-based contribution" in c for c in stored)

        # --- Conclusion prompt renders the real data ------------------
        prompt = get_method("double_crux").get_conclusion_prompt(disc)
        assert ("Distributed teams deliver features as fast as colocated "
                "teams") in prompt
        assert "Belief shifts on the crux (initial → final):" in prompt
        assert "0.25 → 0.6" in prompt

        # --- Persistence sanity ---------------------------------------
        persisted = db_method_state(tmp_db, disc.id)
        assert persisted["current_phase"] == state["current_phase"]
        assert persisted["crux_map"]["verdict"] == "factual"
```

- [ ] **Step 2: Run the new test**

Run: `uv run pytest tests/test_method_flow_e2e.py::TestDoubleCruxFlow -v`
Expected: PASS. Likeliest script failure: the belief-shift formatting assertion (`"0.25 → 0.6"`) — if it fails, print the prompt, match the actual `format_belief_shifts` rendering, and adjust **only** that literal (the shift values themselves are asserted structurally above).

- [ ] **Step 3: Run the whole new file**

Run: `uv run pytest tests/test_method_flow_e2e.py -q`
Expected: 3 passed.

- [ ] **Step 4: Commit**

```bash
git add tests/test_method_flow_e2e.py
git commit -m "test: Double Crux E2E flow run incl. identify->hunt loop-back"
```

---

### Task 4: Tree of Thoughts E2E flow test (score→prune→expand→score loop)

**Files:**
- Modify: `tests/test_method_flow_e2e.py` (append)

**Interfaces:**
- Consumes: Task 1 helpers, unchanged.

**Loop choreography:** propose (T1–T4) → score pass 1 → prune pass 1 (no previous beam → continue; beam `[3, 1, 4]`) → expand → score pass 2 (identical payloads — the off-beam `T2` entry is silently dropped; every survivor freshly re-scored) → prune pass 2 (ordered beam unchanged + full fresh coverage → **converged**) → synthesise. Composites (feasibility + impact + (6 − risk)), meaned over both scorers: T1 12.0, T2 8.5, T3 14.5, T4 11.0.

- [ ] **Step 1: Append the failing ToT test**

Append to `tests/test_method_flow_e2e.py`:

```python
# ---------------------------------------------------------------------------
# Tree of Thoughts — exercises one full score->prune->expand->score loop
# ---------------------------------------------------------------------------

#: Two thoughts per participant, pairwise overlap far below 0.7.
#: Raw order -> T1, T2 from P1; T3, T4 from P2.
TOT_THOUGHTS = {
    "P1": "1. Gamify the onboarding flow with progress rewards\n"
          "2. Rebuild documentation as interactive tutorials",
    "P2": "1. Assign every newcomer a dedicated peer mentor\n"
          "2. Automate environment setup with one-click scripts",
}

#: Identical payloads serve both scoring passes: in pass 2 only the
#: beam (T1, T3, T4) is eligible and the T2 entry is silently dropped,
#: while the unchanged values keep the ordered beam identical — the
#: convergence condition.  Mean composites: T3 14.5 > T1 12.0 > T4 11.0
#: > T2 8.5 -> beam [3, 1, 4].
TOT_SCORES = {
    "P1": ("My scores:\n```json\n"
           '{"scores": {'
           '"T1": {"feasibility": 4, "impact": 4, "risk": 2}, '
           '"T2": {"feasibility": 3, "impact": 3, "risk": 3}, '
           '"T3": {"feasibility": 5, "impact": 4, "risk": 1}, '
           '"T4": {"feasibility": 4, "impact": 3, "risk": 2}}}'
           "\n```"),
    "P2": ("My scores:\n```json\n"
           '{"scores": {'
           '"T1": {"feasibility": 4, "impact": 4, "risk": 2}, '
           '"T2": {"feasibility": 3, "impact": 2, "risk": 3}, '
           '"T3": {"feasibility": 5, "impact": 5, "risk": 1}, '
           '"T4": {"feasibility": 4, "impact": 3, "risk": 2}}}'
           "\n```"),
}

TOT_EXPANSIONS = {
    "P1": ("```json\n"
           '{"expansions": ['
           '{"thought_id": 3, "refinement": "Cap each mentor at two '
           'newcomers per quarter with weekly checkpoints", '
           '"obstacles": ["Mentor time budget"]}, '
           '{"thought_id": 1, "refinement": "Tie progress rewards to '
           'real environment-setup milestones"}]}'
           "\n```"),
    "P2": ("```json\n"
           '{"expansions": ['
           '{"thought_id": 4, "refinement": "Ship a bootstrap script '
           'exercised nightly in CI", '
           '"obstacles": ["Operating-system matrix drift"]}]}'
           "\n```"),
}


def tot_content(disc: Discussion, speaker: Entity) -> str:
    """Scripted turn content for the ToT run, keyed on the live phase."""
    phase = disc.method_state["current_phase"]
    if phase == "propose":
        return TOT_THOUGHTS[speaker.name]
    if phase == "score":
        return TOT_SCORES[speaker.name]
    if phase == "prune":
        return "The computed ranking and surviving beam are shown above."
    if phase == "expand":
        return TOT_EXPANSIONS[speaker.name]
    if phase == "synthesise":
        return "The exploration converged on the peer-mentoring approach."
    pytest.fail(f"unexpected ToT phase {phase!r} for {speaker.name}")


class TestTreeOfThoughtsFlow:
    """ToT lifecycle incl. one full expansion loop ending in convergence."""

    @pytest.mark.asyncio
    async def test_full_run_with_expansion_loop(self, tmp_db):
        disc, moderator, pricing, mod, parts = start_method_discussion(
            tmp_db, "tree_of_thoughts", n_participants=2,
            topic="How might we halve new-engineer onboarding time?",
        )

        trace, result = await run_method(
            disc, moderator, tmp_db, pricing, tot_content)

        # --- Flow: score/prune run twice, expand once ------------------
        assert result.get("method_complete") is True
        assert [phase for phase, _ in trace] == (
            ["propose"] * 2 + ["score"] * 2 + ["prune"]
            + ["expand"] * 2 + ["score"] * 2 + ["prune"]
            + ["synthesise"]
        )
        assert all(name == "Mod" for phase, name in trace
                   if phase in ("prune", "synthesise"))

        # --- Beam history: two passes, identical ordered beams --------
        state = disc.method_state
        assert [h["beam_ids"] for h in state["beam_history"]] == [
            [3, 1, 4], [3, 1, 4]]

        # --- tot_artifact (all numbers computed in code) ---------------
        artifact = state["tot_artifact"]
        assert artifact["stop_reason"] == "converged"
        assert artifact["converged"] is True
        assert artifact["depth"] == 2
        assert artifact["recommendation"] == {
            "id": 3,
            "text": "Assign every newcomer a dedicated peer mentor",
            "composite": 14.5,
        }
        assert len(artifact["final_beam"]) == 3
        assert artifact["caveats"] == []

        # --- Expansions recorded at depth 1 -----------------------------
        expansions = state["expansions"]
        assert len(expansions) == 3
        assert all(e["depth"] == 1 for e in expansions)
        assert {e["thought_id"] for e in expansions} == {1, 3, 4}

        # --- Conclusion prompt renders the real data --------------------
        prompt = get_method("tree_of_thoughts").get_conclusion_prompt(disc)
        assert "T3" in prompt
        assert "14.5" in prompt
        assert "beam stabilised" in prompt

        # --- Persistence sanity -----------------------------------------
        persisted = db_method_state(tmp_db, disc.id)
        assert persisted["current_phase"] == state["current_phase"]
        assert persisted["tot_artifact"]["stop_reason"] == "converged"
```

- [ ] **Step 2: Run the new test**

Run: `uv run pytest tests/test_method_flow_e2e.py::TestTreeOfThoughtsFlow -v`
Expected: PASS. Triage as before — trace/beam mismatches usually mean a scripting error in the score constants; recompute the composites by hand before touching anything.

- [ ] **Step 3: Run the whole new file**

Run: `uv run pytest tests/test_method_flow_e2e.py -q`
Expected: 4 passed.

- [ ] **Step 4: Commit**

```bash
git add tests/test_method_flow_e2e.py
git commit -m "test: Tree of Thoughts E2E flow run incl. expansion loop to convergence"
```

---

### Task 5: Full-suite verification + handover docs

**Files:**
- Modify: `HANDOVER.md` (the "Testing gap" section and header)
- Modify: `ROADMAP.md` (test-count line)

- [ ] **Step 1: Run the complete suite**

Run: `uv run pytest -q`
Expected: **2376 passed** (2372 baseline + the 4 new tests), 0 failures.

- [ ] **Step 2: Update HANDOVER.md**

Replace the "Testing gap (applies to NGT / MCDA / Double Crux / ToT)" section body with a short closed note (and prune it into "What is done" style prose), e.g.:

```markdown
### Testing gap — closed 2026-07-16

- ~~No real-pipeline (`complete_turn`) end-to-end flow test for the four
  newest methods.~~ **Done:** `tests/test_method_flow_e2e.py` +
  `tests/flow_e2e_helpers.py` drive NGT, MCDA, Double Crux, and ToT
  start→`method_complete` through `submit_human_message`/`complete_turn`
  (all-human, free-text path, no stubs), including the Double Crux
  identify→hunt loop-back and a full ToT score→prune→expand→score loop
  ending in convergence — both through the real `advance_phase` path.
  Spec: `docs/superpowers/specs/2026-07-16-method-flow-e2e-tests-design.md`.
```

Update the header's test count (2372 → 2376) and the "Main is at **2372 tests passing**" line to match reality at commit time.

- [ ] **Step 3: Update ROADMAP.md**

In the "Comprehensive test suite" row, update the count ("2372 tests" → "2376 tests") and append "and real-pipeline method-flow E2E runs" to the coverage list.

- [ ] **Step 4: Re-run the suite to confirm docs-only changes broke nothing**

Run: `uv run pytest -q`
Expected: 2376 passed.

- [ ] **Step 5: Commit**

```bash
git add HANDOVER.md ROADMAP.md
git commit -m "docs: close the method-flow E2E testing gap in HANDOVER/ROADMAP"
```

---

## Self-Review (completed by the plan author)

1. **Spec coverage:** driver + all-human roster (Task 1); four scenarios incl. both loops (Tasks 1–4); four assertion layers — flow traces, artifacts, conclusion prompts, persistence — present in every test; evidence-marker turn in DC; `MAX_E2E_TURNS` guard; docs update (Task 5). Structured-path and abort-branch coverage are explicit non-goals.
2. **Placeholder scan:** none — every step carries complete code, exact commands, expected output.
3. **Type consistency:** `content_for(disc: Discussion, speaker: Entity) -> str` used identically in all four tests; `run_method` returns `(trace, result)` everywhere; helper names match between Task 1's module and Tasks 2–4's imports.
