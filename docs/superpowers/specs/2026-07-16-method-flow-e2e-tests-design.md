# End-to-End Flow Tests for the Four Newest Methods — Design

Date: 2026-07-16
Status: approved
Origin: HANDOVER.md "Testing gap" — no real-pipeline (`complete_turn`)
end-to-end flow test exists for Nominal Group Technique, Weighted Decision
Matrix (MCDA), Double Crux, or Tree of Thoughts. No GitHub issue filed
(tracked in HANDOVER.md).

## Problem

The four newest discussion methods have handler-level and
structured-conversion unit coverage, but none is driven through its
*complete lifecycle* — every phase in sequence, from `start_discussion`
to `method_complete` — via the production pipeline
(`submit_human_message` → `complete_turn` → `should_advance_phase` →
`advance_phase` → `apply_method_turn_order`). The historical failure mode
this project keeps re-finding (issues #13, #16, #19) is unit tests feeding
handlers idealized inputs the moderator never produces. The two looping
methods (Double Crux, ToT) additionally have loop routing that only
`tests/test_phase_machine_loops.py`'s toy methods exercise end-to-end
today.

## Goal

One test file driving each of the four methods start→finish through the
real pipeline, including one real loop iteration + exit per looping
method, asserting flow, artifacts, conclusion prompt, and persistence.

## Non-goals (out of scope, unchanged coverage elsewhere)

- Structured tool-call ingestion (`test_structured_output.py`, the four
  `test_*_structured.py` files).
- Give-up/abort branches (`MAX_*_ATTEMPTS`/`MAX_*_ROUNDS` exhaustion).
- ToT depth-budget and degenerate exits; DC `values` and budget-exhausted
  `none` verdicts.
- No production code changes. If a run surfaces a product bug, fix it in
  a separate commit (golden rule 7) with its own regression test.

## Approach (selected: all-human free-text drive)

All-human roster: human moderator + 2 participants (3 for NGT). Human
turns carry method data through each handler's free-text
`process_response` path (JSON blocks via `extract_json_payload`,
MCDA's `1. Name (weight: 4)` lines — every handler retains this path);
the human moderator supplies round summaries via
`complete_turn(..., moderator_summary=...)`. No network, no stubs, no
monkeypatching.

Rejected alternatives: (B) AI roster with scripted
`complete_with_tools` stubs — the payload queue must anticipate the very
turn order the test verifies, plus heavy setup; (C) hybrid — B's
complexity for marginal value. The gap named in HANDOVER is *flow*, not
ingestion; the structured path already has pipeline-level coverage via
`Moderator.generate_turn` in `test_structured_output.py`.

## Architecture

New file `tests/test_method_flow_e2e.py` (precedent:
`tests/test_turn_order_flow.py`). If implementation exceeds the
~500-line guideline, shared driver helpers move to a non-test module
(`tests/flow_e2e_helpers.py`); single file preferred.

- Setup: `_entity` pattern; discussions started through the real
  `start_discussion()` so the first phase's turn order is applied by
  production code (precedent: `TestStartDiscussionTurnOrder`). Nothing
  is pre-seeded into `method_state`.
- Driver: a bounded loop, not a scripted turn list.

```python
while not done and turns < MAX_E2E_TURNS:   # module constant
    speaker = disc.current_speaker
    phase = disc.method_state["current_phase"]
    content = content_for(disc)              # per-method, reads live method_state
    submit_human_message(disc, db, speaker.id, content)
    result = await complete_turn(..., moderator_summary="Summary of the turn.")
    trace.append((phase, speaker.name))      # phase the turn was taken in
    done = result.get("method_complete", False)
```

- Each method's test supplies one `content_for` function computing the
  current speaker's contribution from the **live** `method_state` (crux
  ids, candidate ids, thought labels are assigned by handlers at
  runtime — content cannot be pre-scripted).
- Moderator-only phases need no special casing: the moderator becomes
  `current_speaker` and submits like any participant.
- Every step asserts `"error" not in result` so failures point at the
  exact turn; on `MAX_E2E_TURNS` overrun the test fails printing the
  trace — a regression can never hang the suite.

## Scenarios

**NGT** (3 participants), straight line:
generate (2 ideas each, numbered list) → cluster (moderator submits a
numbered candidate list) → clarify (free text) → allocate (each sums to
exactly `POINTS_PER_VOTER=10`) → rank (moderator, presentational) →
`method_complete`.

**MCDA** (2 participants), straight line:
options (numbered list) → criteria (rounds=2, `1. Cost (weight: 4)` format) →
score (full option × criterion 1–5 JSON) → sensitivity (moderator,
presentational) → decide (moderator, decision JSON →
`decision_artifact`) → `method_complete`.

**Double Crux** (2 participants), exercises the identify loop-back:
positions (free text) → hunt_cruxes (cruxes JSON) → identify_crux pass 1:
verdict `none` → loops back to hunt_cruxes (`crux_search_rounds` → 2) →
hunt round 2 (overlapping cruxes) → identify pass 2: verdict `factual`
with `crux_ids` + shared claim → test_crux (rounds=2, evidence-tracked;
one turn carries an `[evidence: …]` marker to exercise the human inline
grounding path) → resolve (resolution JSON with final `crux_belief`) →
`method_complete`.

**ToT** (2 participants), exercises one full score→prune→expand→score
loop: propose (2 thoughts each) → score pass 1 (all thoughts) → prune
pass 1 (no previous beam → routes to expand; beam = top `BEAM_WIDTH=3`)
→ expand (refinement + obstacles per survivor) → score pass 2 (same
relative order, every survivor freshly re-scored) → prune pass 2
(ordered beam unchanged + full fresh coverage → **converged** →
`tot_artifact`) → synthesise (moderator) → `method_complete`.

## Assertions (four layers per run)

1. **Flow** — the `(phase, speaker)` trace equals the expected sequence;
   moderator-only phases show only the moderator; loop phases appear the
   expected number of times (DC: hunt twice, `crux_search_rounds == 2`;
   ToT: `len(beam_history) == 2`); final result has
   `method_complete: True`.
2. **Artifacts** — NGT: `point_allocations` totals + candidate ranking;
   MCDA: `decision_artifact` chosen option + weighted totals; DC:
   `crux_verdict == "factual"`, `shared_crux` claim, resolutions with
   belief numbers; ToT: `tot_artifact["stop_reason"] == "converged"`
   with recommendation id + composite.
3. **Conclusion prompt** — `get_conclusion_prompt(disc)` renders the
   real collected data (actual totals, winning option name, crux claim
   text, recommended thought label + composite) — guards against a
   formatter silently rendering empty sections.
4. **Persistence sanity** — the DB-persisted `method_state` at
   completion matches the live one on `current_phase` + the method's key
   artifact (issue-#16 convention).

## Known subtleties baked into the content scripts

- Idea/thought texts have low word overlap so similarity clustering
  (#42) does not merge them; lengths ≥ the `MIN_*_LENGTH` constants.
- `test_crux` content assertions tolerate the evidence annotation
  appended to human turns.
- MCDA free-text weights must use the `(weight: N)` suffix — the only
  parsed form.
- ToT convergence requires *every* beam survivor freshly re-scored in
  pass 2; the score script covers the whole beam.

## Testing

This design *is* tests: 4 async test functions (one per method) plus
shared driver helpers, `tmp_db` fixture, `pytest.mark.asyncio`, no
network. TDD applies per project rules: each scenario is written first,
observed to drive the real pipeline (or to fail for a real reason), then
refined; exact free-text payload formats are pinned against the
`extract_*` helpers during implementation.
